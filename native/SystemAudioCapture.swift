import CoreAudio
import Darwin
import Foundation

private let queueCapacity = 256
private var selectedProcessMonitor: DispatchSourceTimer?

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

private final class StreamingMonoResampler {
    private let inputFramesPerOutputFrame: Double
    private var buffer: [Float] = []
    private var bufferStart = 0
    private var nextInputPosition = 0.0

    init(inputSampleRate: Double, outputSampleRate: Double) {
        inputFramesPerOutputFrame = inputSampleRate / outputSampleRate
    }

    func append(_ samples: [Float]) -> [Int16] {
        buffer.append(contentsOf: samples)
        return drain()
    }

    func finish() -> [Int16] {
        if let last = buffer.last {
            buffer.append(last)
        }
        let output = drain()
        buffer.removeAll(keepingCapacity: false)
        return output
    }

    private func drain() -> [Int16] {
        var output: [Int16] = []
        while true {
            let localPosition = nextInputPosition - Double(bufferStart)
            let lower = Int(localPosition.rounded(.down))
            guard lower >= 0, lower + 1 < buffer.count else { break }
            let fraction = Float(localPosition - Double(lower))
            let sample = buffer[lower] + (buffer[lower + 1] - buffer[lower]) * fraction
            output.append(pcm16(sample))
            nextInputPosition += inputFramesPerOutputFrame
        }
        if buffer.count > 1 {
            let consumed = Int(nextInputPosition.rounded(.down)) - bufferStart
            let discard = max(0, min(buffer.count - 1, consumed))
            if discard > 0 {
                buffer.removeFirst(discard)
                bufferStart += discard
            }
        }
        return output
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

private func audioProcessObject(for processID: pid_t) throws -> AudioObjectID {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var requestedPID = processID
    var objectID = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let status = withUnsafePointer(to: &requestedPID) { qualifier in
        AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            UInt32(MemoryLayout<pid_t>.size),
            qualifier,
            &size,
            &objectID
        )
    }
    try check(status, "process_resolve")
    guard objectID != kAudioObjectUnknown else {
        throw CaptureError("selected_app_unavailable")
    }
    return objectID
}

private func eligibleAudioProcessIDs() throws -> [pid_t] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyProcessObjectList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    try check(
        AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size
        ),
        "process_list_size"
    )
    var objects = [AudioObjectID](
        repeating: kAudioObjectUnknown,
        count: Int(size) / MemoryLayout<AudioObjectID>.size
    )
    guard !objects.isEmpty else { return [] }
    let status = objects.withUnsafeMutableBufferPointer { buffer in
        AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            0,
            nil,
            &size,
            buffer.baseAddress!
        )
    }
    try check(status, "process_list")
    return objects.compactMap { objectID in
        var pidAddress = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyPID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var processID: pid_t = 0
        var pidSize = UInt32(MemoryLayout<pid_t>.size)
        let pidStatus = AudioObjectGetPropertyData(
            objectID, &pidAddress, 0, nil, &pidSize, &processID
        )
        return pidStatus == noErr && processID > 0 ? processID : nil
    }
}

@available(macOS 14.2, *)
private final class SystemTapRecorder {
    private let outputSampleRate = 16_000
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
    private var resampler: StreamingMonoResampler!
    private var format = AudioStreamBasicDescription()
    private var maxFrames: UInt64 = 0
    private var firstHostTime: UInt64?
    private var framesWritten: UInt64 = 0
    private var droppedFrames: UInt64 = 0
    private var queuedPackets = 0
    private var writerLagged = false
    private var writerLagReported = false
    private var nonzeroSignalSeen = false
    private var writerErrorReported = false
    private let selectedProcessID: pid_t?
    private let selectedProcessObjectID: AudioObjectID?

    init(path: String, maxSeconds: Int, selectedProcessID: pid_t?) throws {
        self.selectedProcessID = selectedProcessID
        let selectedObjectID = try selectedProcessID.map {
            try audioProcessObject(for: $0)
        }
        self.selectedProcessObjectID = selectedObjectID
        let description = selectedObjectID.map {
            CATapDescription(stereoMixdownOfProcesses: [$0])
        } ?? CATapDescription(stereoGlobalTapButExcludeProcesses: [])
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
            resampler = StreamingMonoResampler(
                inputSampleRate: format.mSampleRate,
                outputSampleRate: Double(outputSampleRate)
            )
            writer = try WaveWriter(path: path, sampleRate: outputSampleRate)
            maxFrames = UInt64(maxSeconds) * UInt64(outputSampleRate)
            try createAggregate(tapUUID: description.uuid)
            try startIO()
        } catch {
            cleanup()
            throw error
        }
    }

    func selectedProcessIsAvailable() -> Bool {
        guard let selectedProcessID, let selectedProcessObjectID else { return true }
        return (try? audioProcessObject(for: selectedProcessID)) == selectedProcessObjectID
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
                writerLagged = true
                statsLock.unlock()
            }
            return
        }
        if statsLock.try() {
            queuedPackets += 1
            if queuedPackets >= queueCapacity * 3 / 4 {
                writerLagged = true
            }
            statsLock.unlock()
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
            defer {
                self?.packetFinished()
                self?.slots.signal()
            }
            self?.write(packet)
        }
    }

    private func packetFinished() {
        statsLock.lock()
        queuedPackets = max(0, queuedPackets - 1)
        statsLock.unlock()
    }

    private func emitWriterLagIfNeeded() {
        statsLock.lock()
        let shouldEmit = writerLagged && !writerLagReported
        if shouldEmit {
            writerLagReported = true
        }
        statsLock.unlock()
        if shouldEmit {
            emit(["event": "writer_lag"])
        }
    }

    private func estimatedFrameCount(_ inputData: UnsafePointer<AudioBufferList>) -> UInt64 {
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inputData))
        guard let first = buffers.first else { return 0 }
        return UInt64(first.mDataByteSize) / UInt64(max(4, Int(format.mBytesPerFrame)))
    }

    private func write(_ packet: AudioPacket) {
        emitWriterLagIfNeeded()
        if firstHostTime == nil {
            firstHostTime = packet.hostTime
            emit(["event": "first_buffer", "host_time_ns": hostTimeNanos(packet.hostTime)])
        }
        guard framesWritten < maxFrames else { return }
        let allowed = packet.frameCount
        var mono = [Float](repeating: 0, count: allowed)
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
                mono[frame] = sum / max(1, count)
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
                    mono[frame] = sum / Float(channels)
                }
            }
        }
        if !nonzeroSignalSeen && mono.contains(where: { abs($0) > 0.0001 }) {
            nonzeroSignalSeen = true
            emit(["event": "nonzero_signal"])
        }
        writeOutput(resampler.append(mono))
    }

    private func writeOutput(_ samples: [Int16]) {
        let allowed = min(samples.count, Int(maxFrames - framesWritten))
        guard allowed > 0 else { return }
        do {
            try writer.write(samples: Array(samples.prefix(allowed)))
            framesWritten &+= UInt64(allowed)
        } catch {
            if !writerErrorReported {
                writerErrorReported = true
                emit(["event": "writer_error"])
            }
        }
    }

    func stop() {
        if let procID, aggregateID != kAudioObjectUnknown {
            AudioDeviceStop(aggregateID, procID)
        }
        cleanup()
        writerQueue.sync {}
        emitWriterLagIfNeeded()
        writeOutput(resampler.finish())
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
        guard #available(macOS 14.2, *) else {
            emit(["event": "error", "reason": "requires_macos_14_2"])
            exit(3)
        }
        if CommandLine.arguments.count == 2,
           CommandLine.arguments[1] == "--list-pids" {
            do {
                emit(["event": "eligible_processes", "pids": try eligibleAudioProcessIDs()])
                exit(0)
            } catch {
                emit(["event": "error", "reason": "process_list_failed"])
                exit(5)
            }
        }
        guard CommandLine.arguments.count == 3 || CommandLine.arguments.count == 5 else {
            emit(["event": "error", "reason": "invalid_arguments"])
            exit(2)
        }
        var selectedProcessID: pid_t?
        if CommandLine.arguments.count == 5 {
            guard CommandLine.arguments[3] == "--pid",
                  let value = Int32(CommandLine.arguments[4]), value > 0 else {
                emit(["event": "error", "reason": "invalid_selected_pid"])
                exit(2)
            }
            selectedProcessID = value
        }
        do {
            let recorder = try SystemTapRecorder(
                path: CommandLine.arguments[1],
                maxSeconds: Int(CommandLine.arguments[2]) ?? 10_800,
                selectedProcessID: selectedProcessID
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
            if selectedProcessID != nil {
                let monitor = DispatchSource.makeTimerSource(queue: .main)
                monitor.schedule(deadline: .now() + 0.5, repeating: 0.5)
                monitor.setEventHandler {
                    guard !recorder.selectedProcessIsAvailable() else { return }
                    emit(["event": "error", "reason": "selected_app_unavailable"])
                    recorder.stop()
                    exit(6)
                }
                monitor.resume()
                selectedProcessMonitor = monitor
            }
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
