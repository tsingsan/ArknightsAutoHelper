import logging
import sys
import threading
import multiprocessing
import queue as threading_Queue

import Arknights.helper
import config
from connector.ADBConnector import ADBConnector, ensure_adb_alive
from util.excutil import format_exception
from typing import Mapping

config.background = True
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())
logger.propagate = False

class WebHandler(logging.Handler):
    terminator = '\n'

    def __init__(self, outq):
        super().__init__()
        self.outq = outq

    def flush(self):
        pass

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            self.outq.put(dict(type="log", message=msg, level=level))
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)


class WorkerThread(threading.Thread):
    def __init__(self, inq: threading_Queue.Queue, outq : multiprocessing.Queue, skip_event : threading.Event, interrupt_event : threading.Event):
        super().__init__()
        self.input = inq
        self.output = outq
        self.device = None
        self.blocking = False
        self.skip_wait_event = skip_event
        self.interrupt_event = interrupt_event
        self.helper = None
        self.allowed_calls = {
            "web:connect": self.web_connect,
            "worker:set_enable_refill": lambda x: setattr(self.helper, 'use_refill', bool(x)),
            "worker:set_refill_with_item": lambda x: setattr(self.helper, 'refill_with_item', bool(x)),
            "worker:set_refill_with_originium": lambda x: setattr(self.helper, 'refill_with_originium', bool(x)),
            "worker:set_max_refill_count": self.set_max_refill_count,
            "worker:module_battle": self.ensure_connector_decorator(lambda stage, count: self.helper.module_battle(stage, int(count))),
            "worker:module_battle_slim": self.ensure_connector_decorator(lambda count: self.helper.module_battle_slim(set_count=int(count))),
            "worker:clear_task": self.ensure_connector_decorator(lambda: self.helper.clear_task()),
            "worker:recruit": self.ensure_connector_decorator(lambda: self.helper.recruit()),
        }

    # threading.Thread
    def run(self):
        print("starting worker thread")
        loghandler = WebHandler(self.output)
        loghandler.setLevel(logging.INFO)
        logging.root.addHandler(loghandler)
        version = config.version
        if config.get_instance_id() != 0:
            version += f" (instance {config.get_instance_id()})"
        self.notify("web:version", version)
        ensure_adb_alive()
        devices = ADBConnector.available_devices()
        devices = ["adb:"+x[0] for x in devices]
        self.notify("web:availiable-devices", devices)
        self.helper = Arknights.helper.ArknightsHelper(frontend=self)
        while True:
            self.notify("worker:idle")
            command : dict = self.input.get(block=True)
            if command.get('type', None) == "call":
                self.interrupt_event.clear()
                self.notify('worker:busy')
                tag = command.get('tag', None)
                action = command.get('action', None)
                return_value = None
                exc = None
                try:
                    func = self.allowed_calls[action]
                    args = command.get('args', [])
                    return_value = func(*args)
                except:
                    exc = sys.exc_info()
                if exc is None:
                    result = dict(type='call-result', status='resolved', tag=tag, return_value=return_value)
                else:
                    result = dict(type='call-result', status='exception', tag=tag, exception=format_exception(*exc))
                if tag is not None:
                    self.output.put_nowait(result)

    # frontend, called by helper
    def attach(self, helper):
        pass
    def alert(self, title, text, level='info', details=None):
        """user-targeted message"""
        logger.info("sending alert %s %s %s %s", level, title, text, details)
        self.output.put(dict(type="alert", title=title, message=text, level=level, details=details))
    def notify(self, name, value=None):
        """program-targeted message"""
        logger.info("sending notify %s %r", name, value)
        self.output.put(dict(type="notify", name=name, value=value))
    def delay(self, secs, allow_skip):
        self.notify("wait", dict(duration=secs, allow_skip=allow_skip))
        try:
            if not allow_skip:
                self.interrupt_event.wait(secs)
            else:
                if self.interrupt_event.is_set():
                    raise KeyboardInterrupt()
                self.skip_wait_event.clear()
                self.skip_wait_event.wait(secs)
            if self.interrupt_event.is_set():
                raise KeyboardInterrupt()
        finally:
            self.notify("wait", dict(duration=0, allow_skip=False))

    
    # called by user
    def web_connect(self, dev:str):
        print(dev.split(':', 1))
        connector_type, cookie = dev.split(':', 1)
        if connector_type != 'adb':
            raise KeyError("unknown connector type " + connector_type)
        new_connector = ADBConnector(cookie)
        connector_str = str(new_connector)
        self.helper.connect_device(new_connector)
        self.notify("web:current-device", connector_str)
    
    def ensure_connector(self):
        if self.helper.adb is None:
            new_connector = ADBConnector.auto_connect()
            self.helper.connect_device(new_connector)
            self.notify("web:current-device", str(new_connector))

    def ensure_connector_decorator(self, func):
        def decorated(*args, **kwargs):
            self.ensure_connector()
            return func(*args, **kwargs)
        return decorated
    
    def set_max_refill_count(self, count):
        self.helper.refill_count = 0
        self.helper.max_refill_count = count



def worker_process(inq : multiprocessing.Queue, outq : multiprocessing.Queue):
    print("starting worker process")
    threadq = threading_Queue.Queue()
    skip_evt = threading.Event()
    intr_evt = threading.Event()
    thr = WorkerThread(threadq, outq, skip_evt, intr_evt)
    thr.setDaemon(True)
    thr.start()
    print("starting worker process loop")

    while True:
        request = inq.get()
        if request is None:
            break
        if not isinstance(request, Mapping):
            outq.put(dict(type="alert", title="RPC Error", text="invalid request object", level="error"))
            break
        req_type = request.get("type", None)
        if req_type == "web:skip":
            skip_evt.set()
        elif req_type == "web:interrupt":
            intr_evt.set()
            skip_evt.set()
        elif req_type == "web:kill":
            import os
            os.kill(os.getpid())
        else:
            threadq.put(request)
    outq.close()
        