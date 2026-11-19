"""Out-of-tree acceptance harness: exercise real Tk buttons on a virtual X display."""
import json
import runpy
import tkinter as tk
from pathlib import Path
from tkinter import ttk

ROOT=Path(__file__).resolve().parents[1]
import os
os.chdir(ROOT)
(ROOT/'results'/'minimal_witness.zip').unlink(missing_ok=True)
root_init=tk.Tk.__init__
state={'first':False,'second':False}

def all_widgets(parent):
    for kid in parent.winfo_children():
        yield kid
        yield from all_widgets(kid)

def patched_init(self,*a,**kw):
    root_init(self,*a,**kw)
    def start():
        buttons=[w for w in all_widgets(self) if isinstance(w,ttk.Button)]
        search={x.cget('text'):x for x in buttons}
        assert '运行审查' in search and '生成最小复现包' in search
        search['运行审查'].invoke()
        def wait_review(attempt=0):
            text=''.join(w.get('1.0','end') for w in all_widgets(self)
                         if isinstance(w, tk.Text))
            if '结果：REGRESSION' in text:
                state['first']=True
                search['生成最小复现包'].invoke()
                self.after(400,wait_reduce)
            elif attempt<40:
                self.after(150,lambda:wait_review(attempt+1))
            else:
                raise RuntimeError('UI review failed: '+text[:400])
        def wait_reduce(attempt=0):
            text=''.join(w.get('1.0','end') for w in all_widgets(self)
                         if isinstance(w,tk.Text))
            if '缩减结果：8 → 3 行' in text:
                state['second']=True
                assert (ROOT/'results/minimal_witness.zip').exists()
                self.quit()
            elif attempt<60:
                self.after(150,lambda:wait_reduce(attempt+1))
            else:
                raise RuntimeError('UI reducer failed: '+text[:500])
        self.after(250,wait_review)
    self.after(500,start)
    self.after(15000,self.quit)

tk.Tk.__init__=patched_init
runpy.run_path(str(ROOT/'启动审查.py'),run_name='__main__')
print(json.dumps(state,ensure_ascii=False))
assert state['first'] and state['second']
