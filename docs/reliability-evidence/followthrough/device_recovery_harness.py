import contextlib,io,json,os,time
from concurrent.futures import ThreadPoolExecutor
from speakeasy import recorder,settings

def check():
    records=[]
    recorder._log_recovery=records.append
    rec=recorder.Recorder()
    helpers=[]
    create=rec._new_helper
    def new():
        helper=create();helpers.append(helper);return helper
    rec._new_helper=new
    result={'build_commit':settings.build_commit(),'scenario':'isolated_helper_exit'}
    try:
        started=time.perf_counter()
        result['prewarm_ok']=rec.prewarm()
        result['prewarm_ms']=(time.perf_counter()-started)*1000
        if not result['prewarm_ok']:
            return result
        helper=helpers[-1]
        helper._process.terminate()
        helper._process.join(1)
        try:
            rec.start()
            result['exit_detected']=False
        except recorder.RecorderBusy:
            result['exit_detected']=True
        started=time.perf_counter()
        result['recovered']=rec.recover('helper_exit')
        result['rearm_ms']=(time.perf_counter()-started)*1000
        result['recovery_records']=records
        if result['recovered']:
            pressed=time.perf_counter_ns()
            rec.start()
            time.sleep(.2)
            audio=rec.stop()
            result['next_take_frames']=len(audio)
            result['first_buffer_after_start_ms']=(rec.first_buffer_ns-pressed)/1e6 if rec.first_buffer_ns else None
            del audio
    finally:
        rec.shutdown()
        result['live_test_helpers_after_shutdown']=sum(h._process.is_alive() for h in helpers)
    return result

if __name__=='__main__':
    if recorder._capture_device_class().authorizationStatusForMediaType_('soun') != 3:
        raise SystemExit('existing microphone authorization required')
    with ThreadPoolExecutor(max_workers=1) as control:
        with contextlib.redirect_stdout(io.StringIO()):
            result=control.submit(check).result(timeout=20)
    fd=os.open('/private/tmp/speakeasy-followthrough/device-recovery.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:json.dump(result,out,indent=2)
    print(json.dumps(result))
