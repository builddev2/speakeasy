import CoreAudio
import Darwin
import Foundation

private let queueCapacity = 256

private struct AudioPacket {
    let hostTime: UInt64
    let frameCount: Int
    let buffers: [Data]
    let channelsPerBuffer: [Int]
}

private final class WaveWriter {
    private let handle: FileHandle
    private let sampleRate: Int
    private var framesWritten: UInt32 = 0

    init(path: String, sampleRate: Int) throws {
        FileManager.default.createFile(atPath: path, contents: nil)
        handle = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
        self.sampleRate = sampleRate
        try handle.write(contentsOf: Data(repeating: 0, count: 44))
    }

    func write(samples: [Int16]) throws {
        try samples.withUnsafeBytes { bytes in
            try handle.write(contentsOf: Data(bytes))
        }
        framesWritten &+= UInt32(samples.count)
    }

    func close() {
        let dataBytes = framesWritten &* 2
        var header = Data()
        header.appendASCII("RIFF")
        header.appendLE(UInt32(36) &+ dataBytes)
        header.appendASCII("WAVEfmt ")
        header.appendLE(UInt32(16))
        header.appendLE(UInt16(1))
        header.appendLE(UInt16(1))
        header.appendLE(UInt32(sampleRate))
        header.appendLE(UInt32(sampleRate * 2))
        header.appendLE(UInt16(2))
        header.appendLE(UInt16(16))
        header.appendASCII("data")
        header.appendLE(dataBytes)
        do {
            try handle.seek(toOffset: 0)
            try handle.write(contentsOf: header)
            try handle.close()
        } catch {
            // The parent treats an empty/truncated spool as an unavailable track.
        }
    }
}

private extension Data {
    mutating func appendASCII(_ value: String) {
        append(value.data(using: .ascii)!)
    }

    mutating func appendLE<T: FixedWidthInteger>(_ value: T) {
        var little = value.littleEndian
        Swift.withUnsafeBytes(of: &little) { append(contentsOf: $0) }
    }
}

@available(macOS 14.2, *)
private final class SystemTapRecorder {
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var procID: AudioDeviceIOProcID?
    private let callbackQueue = DispatchQueue(
        label: "com.jasonchiu.speakeasy.system-audio-callback",
        qos: .userInteractive
    )
    private let writerQueue = DispatchQueue(label: "com.jasonchiu.speakeasy.system-audio-writer")
    private let slots = DispatchSemaphore(value: queueCapacity)
    private let statsLock = NSLock()
    private var writer: WaveWriter!
    private var format = AudioStreamBasicDescription()
    private var maxFrames: UInt64 = 0
    private var firstHostTime: UInt64?
    private var framesWritten: UInt64 = 0
    private var droppedFrames: UInt64 = 0

    init(path: String, maxSeconds: Int) throws {
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.name = "Speakeasy system audio"
        description.isPrivate = true
        description.muteBehavior = .unmuted

        var newTap = AudioObjectID(kAudioObjectUnknown)
        try check(AudioHardwareCreateProcessTap(description, &newTap), "tap_create")
        tapID = newTap

        do {
            format = try Self.readFormat(tapID)
            guard format.mFormatID == kAudioFormatLinearPCM,
                  format.mBitsPerChannel == 32,
                  format.mFormatFlags & kAudioFormatFlagIsFloat != 0 else {
                throw CaptureError("unsupported_format")
            }
            writer = try WaveWriter(path: path, sampleRate: Int(format.mSampleRate.rounded()))
            maxFrames = UInt64(maxSeconds) * UInt64(format.mSampleRate.rounded())
            try createAggregate(tapUUID: description.uuid)
            try startIO()
        } catch {
            cleanup()
            throw error
        }
    }

    private static func readFormat(_ tapID: AudioObjectID) throws -> AudioStreamBasicDescription {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var format = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        try check(AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, &format), "tap_format")
        return format
    }

    private func createAggregate(tapUUID: UUID) throws {
        let description: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Speakeasy system audio",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [] as [[String: Any]],
            kAudioAggregateDeviceTapListKey: [[
                kAudioSubTapUIDKey: tapUUID.uuidString,
                kAudioSubTapDriftCompensationKey: true,
            ]],
        ]
        var newAggregate = AudioObjectID(kAudioObjectUnknown)
        try check(
            AudioHardwareCreateAggregateDevice(description as CFDictionary, &newAggregate),
            "aggregate_create"
        )
        aggregateID = newAggregate
    }

    private func startIO() throws {
        var status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, callbackQueue) {
            [weak self] _, inputData, inputTime, _, _ in
            self?.enqueue(now: inputTime, inputData: inputData)
        }
        try check(status, "io_proc_create")
        guard let procID else { throw CaptureError("io_proc_missing") }
        status = AudioDeviceStart(aggregateID, procID)
        try check(status, "device_start")
    }

    private func enqueue(
        now: UnsafePointer<AudioTimeStamp>,
        inputData: UnsafePointer<AudioBufferList>
    ) {
        guard slots.wait(timeout: .now()) == .success else {
            if statsLock.try() {
                droppedFrames &+= estimatedFrameCount(inputData)
                statsLock.unlock()
            }
            return
        }
        let audioBuffers = UnsafeMutableAudioBufferListPointer(
            UnsafeMutablePointer(mutating: inputData)
        )
        var buffers: [Data] = []
        var channels: [Int] = []
        var frameCount = Int.max
        for buffer in audioBuffers where buffer.mDataByteSize > 0 {
            guard let data = buffer.mData else { continue }
            buffers.append(Data(bytes: data, count: Int(buffer.mDataByteSize)))
            let channelCount = max(1, Int(buffer.mNumberChannels))
            channels.append(channelCount)
            frameCount = min(
                frameCount,
                Int(buffer.mDataByteSize) / (MemoryLayout<Float>.size * channelCount)
            )
        }
        guard !buffers.isEmpty, frameCount > 0, frameCount != Int.max else {
            slots.signal()
            return
        }
        let packet = AudioPacket(
            hostTime: now.pointee.mHostTime == 0 ? mach_absolute_time() : now.pointee.mHostTime,
            frameCount: frameCount,
            buffers: buffers,
            channelsPerBuffer: channels
        )
        writerQueue.async { [weak self] in
            defer { self?.slots.signal() }
            self?.write(packet)
        }
    }

    private func estimatedFrameCount(_ inputData: UnsafePointer<AudioBufferList>) -> UInt64 {
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inputData))
        guard let first = buffers.first else { return 0 }
        return UInt64(first.mDataByteSize) / UInt64(max(4, Int(format.mBytesPerFrame)))
    }

    private func write(_ packet: AudioPacket) {
        if firstHostTime == nil {
            firstHostTime = packet.hostTime
            emit(["event": "first_buffer", "host_time_ns": hostTimeNanos(packet.hostTime)])
        }
        guard framesWritten < maxFrames else { return }
        let allowed = min(packet.frameCount, Int(maxFrames - framesWritten))
        var mono = [Int16](repeating: 0, count: allowed)
        let nonInterleaved = format.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
        if nonInterleaved {
            for frame in 0..<allowed {
                var sum: Float = 0
                var count: Float = 0
                for (index, data) in packet.buffers.enumerated() {
                    let channelCount = packet.channelsPerBuffer[index]
                    data.withUnsafeBytes { raw in
                        let values = raw.bindMemory(to: Float.self)
                        for channel in 0..<channelCount {
                            sum += values[frame * channelCount + channel]
                            count += 1
                        }
                    }
                }
                mono[frame] = pcm16(sum / max(1, count))
            }
        } else {
            let channels = max(1, Int(format.mChannelsPerFrame))
            packet.buffers[0].withUnsafeBytes { raw in
                let values = raw.bindMemory(to: Float.self)
                for frame in 0..<allowed {
                    var sum: Float = 0
                    for channel in 0..<channels {
                        sum += values[frame * channels + channel]
                    }
                    mono[frame] = pcm16(sum / Float(channels))
                }
            }
        }
        do {
            try writer.write(samples: mono)
            framesWritten &+= UInt64(allowed)
        } catch {
            // The parent validates the resulting track before transcription.
        }
    }

    func stop() {
        if let procID, aggregateID != kAudioObjectUnknown {
            AudioDeviceStop(aggregateID, procID)
        }
        cleanup()
        writerQueue.sync {}
        writer.close()
        emit([
            "event": "stopped",
            "frames": framesWritten,
            "dropped_frames": droppedFrames,
        ])
    }

    private func cleanup() {
        if let procID, aggregateID != kAudioObjectUnknown {
            AudioDeviceDestroyIOProcID(aggregateID, procID)
        }
        procID = nil
        if aggregateID != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = AudioObjectID(kAudioObjectUnknown)
        }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }
}

private struct CaptureError: Error {
    let reason: String
    init(_ reason: String) { self.reason = reason }
}

private func check(_ status: OSStatus, _ operation: String) throws {
    if status != noErr {
        throw CaptureError("\(operation)_\(status)")
    }
}

private func pcm16(_ sample: Float) -> Int16 {
    let clamped = min(1, max(-1, sample))
    return Int16(clamped * Float(Int16.max))
}

private func hostTimeNanos(_ hostTime: UInt64) -> UInt64 {
    return AudioConvertHostTimeToNanos(hostTime)
}

private func emit(_ payload: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
    fflush(stdout)
}

@main
private struct SystemAudioCaptureMain {
    static func main() {
        guard CommandLine.arguments.count == 3 else {
            emit(["event": "error", "reason": "invalid_arguments"])
            exit(2)
        }
        guard #available(macOS 14.2, *) else {
            emit(["event": "error", "reason": "requires_macos_14_2"])
            exit(3)
        }
        do {
            let recorder = try SystemTapRecorder(
                path: CommandLine.arguments[1],
                maxSeconds: Int(CommandLine.arguments[2]) ?? 10_800
            )
            emit(["event": "ready"])
            signal(SIGTERM, SIG_IGN)
            signal(SIGINT, SIG_IGN)
            let stopSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
            stopSource.setEventHandler {
                recorder.stop()
                exit(0)
            }
            stopSource.resume()
            dispatchMain()
        } catch let error as CaptureError {
            emit(["event": "error", "reason": error.reason])
            exit(4)
        } catch {
            emit(["event": "error", "reason": "capture_start_failed"])
            exit(5)
        }
    }
}
