#!/usr/bin/env python3
# PulseSystemInfoheavy.py
# Full-featured, heavy System Monitor (PyQt5 + psutil + pyqtgraph)
# Features:
#  - CPU, RAM, Disk usage (progress bars + numeric)
#  - Per-partition disk details with progress bars
#  - Network upload/download instantaneous speeds (KB/s or MB/s)
#  - Live time-series charts for CPU, RAM, Network and Disk (%) using pyqtgraph
#  - Process list with search/filter, show top N by CPU or RAM, per-process CPU/RAM bars
#  - Configurable update interval (default 1s)
#  - Optimized to avoid heavy blocking operations; keeps UI responsive
#
# Requirements:
#   pip install pyqt5 psutil pyqtgraph
#
# Run:
#   python PulseSystemInfoheavy.py
#
from __future__ import annotations
import sys
import time
import psutil
from collections import deque
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QListWidget, QListWidgetItem, QLineEdit, QComboBox, QSpinBox, QPushButton,
    QGridLayout
)
from PyQt5.QtCore import Qt, QTimer
import pyqtgraph as pg
import shutil, os, math, platform, subprocess

APP_UPDATE_INTERVAL_MS_DEFAULT = 1000  # default 1 second
MAX_HISTORY = 120  # keep 120 samples for charts (~2 minutes at 1s interval)

def human_readable_bytes(n):
    if n is None:
        return "0 B"
    step = 1024.0
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while n >= step and i < len(units) - 1:
        n /= step
        i += 1
    return f"{n:.1f} {units[i]}"

class SystemData:
    def __init__(self):
        self.last_net = psutil.net_io_counters()
        self.last_time = time.time()
        self.cpu_hist = deque(maxlen=MAX_HISTORY)
        self.ram_hist = deque(maxlen=MAX_HISTORY)
        self.disk_hist = deque(maxlen=MAX_HISTORY)
        self.net_sent_hist = deque(maxlen=MAX_HISTORY)
        self.net_recv_hist = deque(maxlen=MAX_HISTORY)
        for _ in range(MAX_HISTORY):
            self.cpu_hist.append(0.0)
            self.ram_hist.append(0.0)
            self.disk_hist.append(0.0)
            self.net_sent_hist.append(0.0)
            self.net_recv_hist.append(0.0)

    def sample(self):
        now = time.time()
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        total_used = 0
        total_total = 0
        try:
            parts = psutil.disk_partitions(all=False)
            for p in parts:
                try:
                    u = psutil.disk_usage(p.mountpoint)
                    total_used += u.used
                    total_total += u.total
                except Exception:
                    continue
            disk_percent = (total_used / total_total) * 100.0 if total_total > 0 else 0.0
        except Exception:
            try:
                disk_percent = psutil.disk_usage("/").percent
            except Exception:
                disk_percent = 0.0
        net = psutil.net_io_counters()
        elapsed = now - self.last_time if now - self.last_time > 0 else 1.0
        sent_rate = (net.bytes_sent - self.last_net.bytes_sent) / elapsed
        recv_rate = (net.bytes_recv - self.last_net.bytes_recv) / elapsed
        self.last_net = net
        self.last_time = now
        self.cpu_hist.append(cpu)
        self.ram_hist.append(ram)
        self.disk_hist.append(disk_percent)
        self.net_sent_hist.append(sent_rate)
        self.net_recv_hist.append(recv_rate)
        return {"cpu": cpu, "ram": ram, "disk_percent": disk_percent, "net_sent": sent_rate, "net_recv": recv_rate}

class PartitionWidget(QWidget):
    def __init__(self, device, mountpoint, percent, used, total):
        super().__init__()
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        self.label = QLabel(f"{device} ({mountpoint})")
        self.pbar = QProgressBar(); self.pbar.setMaximum(100); self.pbar.setValue(int(min(100, percent)))
        self.info = QLabel(f"{percent:.1f}% ({human_readable_bytes(used)} / {human_readable_bytes(total)})")
        layout.addWidget(self.label, 3)
        layout.addWidget(self.pbar, 2)
        layout.addWidget(self.info, 2)
        self.setLayout(layout)

class ProcessItemWidget(QWidget):
    def __init__(self, name, pid, cpu, mem):
        super().__init__()
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        self.nameLabel = QLabel(f"{name} (PID {pid})")
        self.nameLabel.setToolTip(self.nameLabel.text())
        self.cpuBar = QProgressBar(); self.cpuBar.setMaximum(100); self.cpuBar.setValue(int(min(100, cpu))); self.cpuBar.setFormat(f"CPU: {cpu:.1f}%")
        self.memBar = QProgressBar(); self.memBar.setMaximum(100); self.memBar.setValue(int(min(100, mem))); self.memBar.setFormat(f"RAM: {mem:.1f}%")
        layout.addWidget(self.nameLabel, 3)
        layout.addWidget(self.cpuBar, 1)
        layout.addWidget(self.memBar, 1)
        self.setLayout(layout)

class PulseSystemInfoApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pulse System Info - Heavy")
        self.setGeometry(120, 80, 1100, 650)
        central = QWidget(); self.setCentralWidget(central)
        main_layout = QHBoxLayout(); central.setLayout(main_layout)
        left_v = QVBoxLayout(); main_layout.addLayout(left_v, 3)
        self.cpu_label = QLabel("CPU: 0.0%"); self.cpu_bar = QProgressBar(); self.cpu_bar.setMaximum(100)
        self.ram_label = QLabel("RAM: 0.0%"); self.ram_bar = QProgressBar(); self.ram_bar.setMaximum(100)
        self.disk_label = QLabel("Disk: 0.0%"); self.disk_bar = QProgressBar(); self.disk_bar.setMaximum(100)
        left_v.addWidget(self.cpu_label); left_v.addWidget(self.cpu_bar)
        left_v.addWidget(self.ram_label); left_v.addWidget(self.ram_bar)
        left_v.addWidget(self.disk_label); left_v.addWidget(self.disk_bar)
        net_h = QHBoxLayout(); self.net_up_label = QLabel("Up: 0 B/s"); self.net_down_label = QLabel("Down: 0 B/s")
        net_h.addWidget(self.net_up_label); net_h.addWidget(self.net_down_label); left_v.addLayout(net_h)
        left_v.addWidget(QLabel("Disk Partitions:"))
        self.partitions_list = QListWidget(); left_v.addWidget(self.partitions_list, 2)
        ctrl_grid = QGridLayout(); left_v.addLayout(ctrl_grid)
        ctrl_grid.addWidget(QLabel("Filter:"), 0, 0); self.search_input = QLineEdit(); self.search_input.setPlaceholderText("Name or PID..."); ctrl_grid.addWidget(self.search_input, 0, 1)
        ctrl_grid.addWidget(QLabel("Sort:"), 0, 2); self.sort_combo = QComboBox(); self.sort_combo.addItems(["CPU","RAM","PID","Name"]); ctrl_grid.addWidget(self.sort_combo, 0, 3)
        ctrl_grid.addWidget(QLabel("Top N:"), 1, 0); self.top_spin = QSpinBox(); self.top_spin.setMinimum(5); self.top_spin.setMaximum(500); self.top_spin.setValue(30); ctrl_grid.addWidget(self.top_spin, 1, 1)
        ctrl_grid.addWidget(QLabel("Interval (ms):"), 1, 2); self.interval_spin = QSpinBox(); self.interval_spin.setMinimum(200); self.interval_spin.setMaximum(10000); self.interval_spin.setValue(APP_UPDATE_INTERVAL_MS_DEFAULT); ctrl_grid.addWidget(self.interval_spin, 1, 3)
        self.apply_btn = QPushButton("Apply"); ctrl_grid.addWidget(self.apply_btn, 2, 3); self.refresh_btn = QPushButton("Refresh Now"); ctrl_grid.addWidget(self.refresh_btn, 2, 1)
        left_v.addWidget(QLabel("Processes: (double-click to open executable location)")); self.process_list = QListWidget(); left_v.addWidget(self.process_list, 6)
        right_v = QVBoxLayout(); main_layout.addLayout(right_v, 5)
        pg.setConfigOptions(antialias=True)
        self.cpu_plot = pg.PlotWidget(title="CPU (%) - history"); self.cpu_plot.setYRange(0,100)
        self.ram_plot = pg.PlotWidget(title="RAM (%) - history"); self.ram_plot.setYRange(0,100)
        self.disk_plot = pg.PlotWidget(title="Disk (%) - history"); self.disk_plot.setYRange(0,100)
        self.net_plot = pg.PlotWidget(title="Network (KB/s) - history"); self.net_plot.addLegend(); self.net_plot.setLabel('left','KB/s')
        self.cpu_curve = self.cpu_plot.plot(pen=pg.mkPen('y', width=1.5))
        self.ram_curve = self.ram_plot.plot(pen=pg.mkPen('c', width=1.5))
        self.disk_curve = self.disk_plot.plot(pen=pg.mkPen('m', width=1.5))
        self.net_sent_curve = self.net_plot.plot(name='Sent', pen=pg.mkPen('g', width=1.2))
        self.net_recv_curve = self.net_plot.plot(name='Recv', pen=pg.mkPen('r', width=1.2))
        right_v.addWidget(self.cpu_plot,1); right_v.addWidget(self.ram_plot,1); right_v.addWidget(self.disk_plot,1); right_v.addWidget(self.net_plot,1)
        self.status = self.statusBar(); self.data = SystemData(); self.timer = QTimer(); self.timer.setInterval(APP_UPDATE_INTERVAL_MS_DEFAULT)
        self.timer.timeout.connect(self.update_all); self.timer.start()
        self.search_input.textChanged.connect(self.update_process_list)
        self.top_spin.valueChanged.connect(self.update_process_list)
        self.sort_combo.currentIndexChanged.connect(self.update_process_list)
        self.apply_btn.clicked.connect(self.apply_settings)
        self.refresh_btn.clicked.connect(self.force_refresh)
        self.process_list.itemDoubleClicked.connect(self.on_process_double_click)
        self.interval_spin.valueChanged.connect(self.on_interval_change)
        for p in psutil.process_iter(): 
            try: p.cpu_percent(interval=None)
            except Exception: pass
        self.update_all()

    def on_interval_change(self, val): self.timer.setInterval(val)
    def apply_settings(self): self.timer.setInterval(self.interval_spin.value()); self.update_all(); self.status.showMessage("Settings applied", 2000)
    def format_bytes_per_sec(self, bps): return f"{bps:.1f} B/s" if bps<1024 else f"{bps/1024:.1f} KB/s" if bps/1024<1024 else f"{bps/1024/1024:.1f} MB/s"
    def update_partitions(self):
        self.partitions_list.clear()
        try: parts = psutil.disk_partitions(all=False)
        except Exception: parts = []
        for p in parts:
            try: u = psutil.disk_usage(p.mountpoint)
            except Exception: continue
            w = PartitionWidget(p.device, p.mountpoint, u.percent, u.used, u.total)
            item = QListWidgetItem(self.partitions_list); item.setSizeHint(w.sizeHint()); self.partitions_list.addItem(item); self.partitions_list.setItemWidget(item,w)
    def update_all(self):
        s = self.data.sample(); cpu, ram, disk_percent, sent, recv = s['cpu'], s['ram'], s['disk_percent'], s['net_sent'], s['net_recv']
        self.cpu_label.setText(f"CPU: {cpu:.1f}%"); self.cpu_bar.setValue(int(min(100,cpu)))
        vm = psutil.virtual_memory(); self.ram_label.setText(f"RAM: {vm.percent:.1f}% ({human_readable_bytes(vm.used)} / {human_readable_bytes(vm.total)})"); self.ram_bar.setValue(int(min(100,vm.percent)))
        self.disk_label.setText(f"Disk: {disk_percent:.1f}%"); self.disk_bar.setValue(int(min(100,disk_percent)))
        self.net_up_label.setText("Up: "+self.format_bytes_per_sec(sent)); self.net_down_label.setText("Down: "+self.format_bytes_per_sec(recv))
        self.update_partitions(); x=list(range(-len(self.data.cpu_hist)+1,1)); self.cpu_curve.setData(x,list(self.data.cpu_hist))
        self.ram_curve.setData(x,list(self.data.ram_hist)); self.disk_curve.setData(x,list(self.data.disk_hist))
        self.net_sent_curve.setData(x,[s/1024.0 for s in self.data.net_sent_hist]); self.net_recv_curve.setData(x,[r/1024.0 for r in self.data.net_recv_hist])
        self.update_process_list()
    def update_process_list(self):
        q = self.search_input.text().strip().lower(); top_n=int(self.top_spin.value()); s=self.sort_combo.currentText(); procs=[]
        for p in psutil.process_iter(['pid','name']):
            try: info = p.info; pid=info.get('pid'); name=info.get('name') or str(pid); cpu=p.cpu_percent(interval=None); mem=p.memory_percent(); procs.append((name,pid,cpu,mem,p))
            except (psutil.NoSuchProcess, psutil.AccessDenied): continue
        if s=="CPU": procs.sort(key=lambda t:t[2],reverse=True)
        elif s=="RAM": procs.sort(key=lambda t:t[3],reverse=True)
        elif s=="PID": procs.sort(key=lambda t:t[1])
        else: procs.sort(key=lambda t:(t[0] or "").lower())
        if q: procs=[t for t in procs if q in (t[0] or '').lower() or q==str(t[1])]
        procs=procs[:top_n]; self.process_list.clear()
        for name,pid,cpu,mem,p in procs: w=ProcessItemWidget(name,pid,cpu,mem); item=QListWidgetItem(self.process_list); item.setSizeHint(w.sizeHint()); self.process_list.addItem(item); self.process_list.setItemWidget(item,w)
    def force_refresh(self): self.data.sample(); self.update_all(); self.status.showMessage("Refreshed",1000)
    def on_process_double_click(self,item):
        w=self.process_list.itemWidget(item); 
        if not w: return
        text=w.nameLabel.text()
        if "(PID" in text:
            try:
                pid=int(text.split("PID")[1].split(")")[0])
                p=psutil.Process(pid)
                exe=p.exe() if hasattr(p,'exe') else None
                if exe and os.path.exists(exe):
                    if platform.system() == "Windows": os.startfile(os.path.dirname(exe))
                    else: subprocess.Popen(['xdg-open',os.path.dirname(exe)])
            except Exception: pass

if __name__=="__main__":
    app=QApplication(sys.argv)
    win=PulseSystemInfoApp()
    win.show()
    sys.exit(app.exec_())
