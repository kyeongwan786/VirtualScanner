"""Keep legacy Tk message boxes inside the webview that owns the backend."""
from __future__ import annotations

import json
import threading
import uuid
from tkinter import messagebox


class WebDialogs:
    def __init__(self, root):
        self.root = root
        self.pending = {}
        self.lock = threading.Lock()
        messagebox._show = self.show

    def show(self, title=None, message=None, _icon=None, _type=None, **options):
        dialog_id = uuid.uuid4().hex
        kind = _type or options.get("type", "ok")
        buttons = {
            "yesno": [("yes", "확인"), ("no", "취소")],
            "yesnocancel": [("yes", "예"), ("no", "아니요"), ("cancel", "취소")],
            "okcancel": [("ok", "확인"), ("cancel", "취소")],
            "retrycancel": [("retry", "다시 시도"), ("cancel", "취소")],
        }.get(kind, [("ok", "확인")])
        event = threading.Event()
        row = {"id": dialog_id, "title": title or "안내", "message": message or "",
               "buttons": buttons, "event": event, "answer": buttons[-1][0]}
        with self.lock:
            self.pending[dialog_id] = row
        # Keep Tk event dispatch alive while the HTML dialog is visible.
        import tkinter as tk
        done = tk.BooleanVar(master=self.root, value=False)
        def poll():
            if event.is_set():
                done.set(True)
            else:
                self.root.after(50, poll)
        self.root.after(0, poll)
        try:
            self.root.wait_variable(done)
            return row["answer"]
        finally:
            with self.lock:
                self.pending.pop(dialog_id, None)

    def handle(self, handler):
        if handler.path != "/api/dialog":
            return False
        if not handler._authorized():
            handler._reply(403, {"error": "unauthorized"})
            return True
        if handler.command == "GET":
            with self.lock:
                row = next(iter(self.pending.values()), None)
                result = {key: row[key] for key in ("id", "title", "message", "buttons")} if row else None
            handler._reply(200, {"dialog": result})
        else:
            try:
                length = int(handler.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("invalid dialog response")
                payload = json.loads(handler.rfile.read(length))
                with self.lock:
                    row = self.pending.get(payload.get("id"))
                    if row and payload.get("answer") in {item[0] for item in row["buttons"]}:
                        row["answer"] = payload["answer"]
                        row["event"].set()
                handler._reply(200, {"ok": True})
            except (ValueError, TypeError) as exc:
                handler._reply(400, {"error": str(exc)})
        return True


CLIENT = r"""
<script>
(() => {
  let chain = Promise.resolve();
  function show(title, message, buttons) {
    const result = chain.then(() => new Promise(resolve => {
      const dialog = document.createElement('dialog');
      dialog.style.cssText = 'width:min(480px,85vw);max-height:80vh;overflow:auto;padding:24px;border:1px solid #d0d5dd;border-radius:14px;color:#182230;background:white;box-shadow:0 20px 70px #0005';
      const heading = document.createElement('h3'); heading.textContent = title;
      const content = document.createElement('p'); content.textContent = message;
      content.style.cssText = 'white-space:pre-wrap;line-height:1.65;font-size:14px';
      const actions = document.createElement('div'); actions.style.cssText='display:flex;gap:10px;justify-content:flex-end;margin-top:20px';
      const finish = value => { dialog.close(); dialog.remove(); resolve(value); };
      for (const [value,label] of buttons) {
        const button=document.createElement('button'); button.textContent=label;
        button.className='button'; button.style.cssText='padding:9px 18px;cursor:pointer';
        button.onclick=()=>finish(value); actions.append(button);
      }
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(buttons[buttons.length-1][0]);});
      dialog.append(heading,content,actions); document.body.append(dialog); dialog.showModal();
    }));
    chain=result.catch(()=>{}); return result;
  }
  window.frameConfirm = message => show('확인',message,[['yes','확인'],['no','취소']]).then(value=>value==='yes');
  let active = null;
  async function poll() {
    try {
      const {dialog}=await request('/api/dialog');
      if(dialog && dialog.id!==active) {
        active=dialog.id;
        const answer=await show(dialog.title,dialog.message,dialog.buttons);
        // Retry the same answer on network errors; never repeat the action itself.
        for (;;) {
          try { await request('/api/dialog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:dialog.id,answer})}); break; }
          catch (_) { await new Promise(resolve=>setTimeout(resolve,500)); }
        }
      }
      if(!dialog)active=null;
    } catch (_) {}
    setTimeout(poll,200);
  }
  poll();
})();
</script>
""".encode("utf-8")
