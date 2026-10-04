#!/usr/bin/env python3
"""智能放药机操作界面。

将本文件放在 Raspberry_BusServoControl_demo 目录中，运行：
    python3 ui4.py

程序会在同目录自动创建 medicine_counts.csv，用于保存库存与累计出药量。
"""

import csv
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from gpiozero import DigitalInputDevice, Motor
import ServoControl


MEDICINES = ("A", "B", "C", "D", "E", "F")

# A～F沿物理排列反向旋转；每个舵机与其光电传感器仍保持成对。
# A=(舵机6, GPIO26), B=(舵机1, GPIO17), C=(舵机2, GPIO18)
# D=(舵机3, GPIO23), E=(舵机4, GPIO24), F=(舵机5, GPIO25)
SENSOR_PINS = (17, 18, 23, 24, 25, 26)
SERVO_IDS = (6, 1, 2, 3, 4, 5)

CSV_PATH = Path(__file__).resolve().with_name("medicine_counts.csv")
MOVE_TIME_MS = 1000
SENSOR_DECISION_WAIT_SECONDS = 1.0
SHAKE_SECONDS = 3.0
SHAKE_FREQUENCY = 5.0
MIN_SENSOR_INTERVAL = 0.02
MAX_FAILED_POSITIONS = 6


class MedicineUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart Medicine Dispenser")
        self.root.geometry("900x560")
        self.root.minsize(820, 520)

        self.data_lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.ui_queue = queue.Queue()
        self.closing = False
        self.busy = False

        self.shaking = False
        self.shake_thread = None
        self.shake_stop_event = threading.Event()
        self.records = self.load_records()
        self.sensor_counts = [0] * 6
        self.last_sensor_times = [0.0] * 6
        self.sensor_events = [threading.Event() for _ in range(6)]

        # 当前出药任务单独计数，避免药物在两个位置判断之间掉落时被漏判。
        self.active_drop_index = None
        self.active_drop_detected = 0

        # 每个舵机均从0开始，并独立保存当前位置和往返方向。
        self.servo_positions = [0] * 6
        self.servo_directions = [1] * 6

        self.selected_medicine = 0
        self.selected_quantity = 1
        self.total_vars = [tk.StringVar(value=str(r["total"])) for r in self.records]
        self.remaining_vars = [tk.StringVar() for _ in MEDICINES]
        self.status_var = tk.StringVar(value="System Ready")
        self.medicine_var = tk.StringVar(value="A")
        self.quantity_var = tk.StringVar(value="1")
        self.save_jobs = [None] * 6

        self.motor_1 = None
        self.motor_2 = None
        self.sensors = []

        self.configure_style()
        self.build_ui()
        self.refresh_remaining()
        self.initialize_hardware()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self.process_ui_queue)

    def configure_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f4f6f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Title.TLabel", font=("Arial", 23, "bold"),
                        background="#f4f6f8", foreground="#1f2937")
        style.configure("Heading.TLabel", font=("Arial", 13, "bold"),
                        background="#ffffff", foreground="#374151")
        style.configure("Value.TLabel", font=("Arial", 30, "bold"),
                        background="#ffffff", foreground="#111827")
        style.configure("Status.TLabel", font=("Arial", 11),
                        background="#f4f6f8", foreground="#4b5563")
        style.configure("Action.TButton", font=("Arial", 13, "bold"), padding=(20, 12))
        style.configure("Adjust.TButton", font=("Arial", 12, "bold"), padding=(1, 1), width=2)
        style.configure("Clear.TButton", font=("Arial", 11), padding=(14, 7))

    def build_ui(self):
        self.root.configure(bg="#f4f6f8")
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 18))
        ttk.Label(header, text="Smart Medicine Dispenser", style="Title.TLabel").pack(side="left")
        self.clear_button = ttk.Button(
            header, text="Clear", style="Clear.TButton", command=self.clear_all
        )
        self.clear_button.pack(side="right")

        controls = ttk.Frame(outer, style="Card.TFrame", padding=22)
        controls.pack(fill="x", pady=(0, 18))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)

        self.shake_button = ttk.Button(
            controls, text="Shake\nRefill Medicine", style="Action.TButton", command=self.start_shake
        )
        self.shake_button.grid(row=0, column=0, rowspan=2, padx=(0, 26), sticky="nsew")

        ttk.Label(controls, text="Select Medicine", style="Heading.TLabel").grid(
            row=0, column=1, pady=(0, 8)
        )
        medicine_adjust = ttk.Frame(controls, style="Card.TFrame")
        medicine_adjust.grid(row=1, column=1)
        ttk.Button(medicine_adjust, text="−", style="Adjust.TButton", width=2,
                   command=lambda: self.change_medicine(-1)).pack(side="left")
        ttk.Label(medicine_adjust, textvariable=self.medicine_var,
                  style="Value.TLabel", width=3, anchor="center").pack(side="left", padx=12)
        ttk.Button(medicine_adjust, text="+", style="Adjust.TButton", width=2,
                   command=lambda: self.change_medicine(1)).pack(side="left")

        ttk.Label(controls, text="Dispense Quantity (1-9)", style="Heading.TLabel").grid(
            row=0, column=2, pady=(0, 8)
        )
        quantity_adjust = ttk.Frame(controls, style="Card.TFrame")
        quantity_adjust.grid(row=1, column=2)
        ttk.Button(quantity_adjust, text="−", style="Adjust.TButton", width=2,
                   command=lambda: self.change_quantity(-1)).pack(side="left")
        ttk.Label(quantity_adjust, textvariable=self.quantity_var,
                  style="Value.TLabel", width=3, anchor="center").pack(side="left", padx=12)
        ttk.Button(quantity_adjust, text="+", style="Adjust.TButton", width=2,
                   command=lambda: self.change_quantity(1)).pack(side="left")

        self.drop_button = ttk.Button(
            controls, text="Drop\nDispense", style="Action.TButton", command=self.start_drop
        )
        self.drop_button.grid(row=0, column=3, rowspan=2, padx=(26, 0), sticky="nsew")

        table_card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        table_card.pack(fill="both", expand=True)
        table_card.columnconfigure(0, weight=1)
        for col in range(1, 7):
            table_card.columnconfigure(col, weight=1)

        ttk.Label(table_card, text="Medicine", style="Heading.TLabel", anchor="center").grid(
            row=0, column=0, sticky="nsew", padx=4, pady=7
        )
        for i, name in enumerate(MEDICINES):
            ttk.Label(table_card, text=name, style="Heading.TLabel", anchor="center").grid(
                row=0, column=i + 1, sticky="nsew", padx=4, pady=7
            )

        ttk.Label(table_card, text="Total Stock", style="Heading.TLabel", anchor="center").grid(
            row=1, column=0, sticky="nsew", padx=4, pady=8
        )
        for i, variable in enumerate(self.total_vars):
            entry = ttk.Entry(table_card, textvariable=variable, justify="center", width=10,
                              font=("Arial", 13))
            entry.grid(row=1, column=i + 1, sticky="ew", padx=4, pady=8, ipady=7)
            entry.bind("<Return>", lambda _event, index=i: self.commit_total(index))
            entry.bind("<FocusOut>", lambda _event, index=i: self.commit_total(index))
            variable.trace_add("write", lambda *_args, index=i: self.schedule_total_save(index))

        ttk.Label(table_card, text="Remaining", style="Heading.TLabel", anchor="center").grid(
            row=2, column=0, sticky="nsew", padx=4, pady=8
        )
        for i, variable in enumerate(self.remaining_vars):
            ttk.Label(table_card, textvariable=variable, background="#eef2f7",
                      foreground="#111827", font=("Arial", 13, "bold"), anchor="center").grid(
                row=2, column=i + 1, sticky="nsew", padx=4, pady=8, ipady=9
            )

        ttk.Separator(outer).pack(fill="x", pady=(16, 8))
        ttk.Label(outer, textvariable=self.status_var, style="Status.TLabel").pack(fill="x")

    def initialize_hardware(self):
        try:
            self.motor_1 = Motor(forward=14, backward=15)
            self.motor_2 = Motor(forward=22, backward=27)

            for index, pin in enumerate(SENSOR_PINS):
                sensor = DigitalInputDevice(pin=pin, pull_up=True, bounce_time=0.003)
                sensor.when_activated = self.make_sensor_callback(index)
                self.sensors.append(sensor)
            self.status_var.set("System Ready: Motors and six optical sensors connected")
        except Exception as exc:
            self.set_controls_enabled(False)
            self.status_var.set(f"Hardware Initialization Failed: {exc}")
            messagebox.showerror("Hardware Initialization Failed", str(exc))

    def make_sensor_callback(self, index):
        def detected():
            now = time.monotonic()
            with self.data_lock:
                if now - self.last_sensor_times[index] < MIN_SENSOR_INTERVAL:
                    return
                self.last_sensor_times[index] = now
                self.sensor_counts[index] += 1
                self.records[index]["dispensed"] += 1
                if self.active_drop_index == index:
                    self.active_drop_detected += 1
                self.save_records_locked()
                actual = self.records[index]["dispensed"]
            self.sensor_events[index].set()
            self.ui_queue.put(("sensor", index, actual))
        return detected

    def start_shake(self):
        # Shake/Motor 独立运行，不占用 busy，其他按钮仍可操作。
        if self.shaking:
            self.shaking = False
            self.shake_stop_event.set()
            self.shake_button.config(text="Shake")
            self.status_var.set("Stopping shake...")
            return

        # 上一次线程尚未完全退出时，不重复创建振动线程。
        if self.shake_thread is not None and self.shake_thread.is_alive():
            self.status_var.set("Stopping shake, please wait...")
            return

        self.shaking = True
        self.shake_stop_event.clear()
        self.shake_button.config(text="Stop Shake")
        self.status_var.set("Shaking...")

        self.shake_thread = threading.Thread(
            target=self.shake_worker,
            daemon=True
        )
        self.shake_thread.start()

    def start_drop(self):
        index = self.selected_medicine
        quantity = self.selected_quantity
        self.commit_all_totals()
        with self.data_lock:
            remaining = self.records[index]["total"] - self.records[index]["dispensed"]
        if remaining < quantity:
            messagebox.showwarning(
                "Insufficient Stock", f"{MEDICINES[index]} medicine has {max(remaining, 0)} pills remaining; cannot dispense {quantity} pills."
            )
            return
        if not self.begin_operation(f"Dispensing {MEDICINES[index]} medicine, target {quantity} pills..."):
            return
        with self.data_lock:
            self.active_drop_index = index
            self.active_drop_detected = 0
        self.sensor_events[index].clear()
        threading.Thread(target=self.drop_worker, args=(index, quantity), daemon=True).start()

    def shake_worker(self):
        error_message = None
        try:
            period = 1.0 / SHAKE_FREQUENCY
            half_period = period / 2.0

            while not self.shake_stop_event.is_set() and not self.closing:
                self.motor_1.forward(1.0)
                self.motor_2.forward(1.0)
                if self.shake_stop_event.wait(half_period):
                    break

                if self.closing:
                    break

                self.motor_1.backward(1.0)
                self.motor_2.backward(1.0)
                if self.shake_stop_event.wait(half_period):
                    break

        except Exception as exc:
            error_message = f"Shake failed: {exc}"

        finally:
            self.shaking = False
            if self.motor_1 is not None:
                self.motor_1.stop()

            if self.motor_2 is not None:
                self.motor_2.stop()

            if not self.closing:
                if error_message is None:
                    self.ui_queue.put(("shake_stopped", None))
                else:
                    self.ui_queue.put(("shake_error", error_message))

    def drop_worker(self, index, target_quantity):
        delivered = 0
        consecutive_failed_positions = 0
        try:
            while delivered < target_quantity and not self.closing:
                # 先接收两个位置判断之间可能稍晚到达的光电信号。
                before = self.get_active_drop_detected(index)
                if before > delivered:
                    delivered = before
                    consecutive_failed_positions = 0
                    if delivered >= target_quantity:
                        self.ui_queue.put((
                            "operation_done",
                            f"{MEDICINES[index]} medicine dispensing completed. Detected {delivered} pills"
                        ))
                        return

                self.sensor_events[index].clear()
                position = self.advance_servo_position(index)

                ServoControl.setMoreBusServoMove(
                    [SERVO_IDS[index], position], 1, MOVE_TIME_MS
                )
                self.ui_queue.put((
                    "status",
                    f"{MEDICINES[index]} medicine servo moved to {position}, waiting for pill detection..."
                ))

                # 第一步：等待舵机完成本次250 position的运动。
                time.sleep(MOVE_TIME_MS / 1000.0)

                # 第二步：舵机到位后，固定等待1秒让药物完成下落。
                # 必须等满这一段时间之后，才能根据光电结果决定是否继续旋转。
                time.sleep(SENSOR_DECISION_WAIT_SECONDS)

                # 第三步：读取本次旋转后的光电结果并作出决策。
                current_detected = self.get_active_drop_detected(index)
                detected = current_detected - before

                if detected > 0:
                    delivered = current_detected
                    consecutive_failed_positions = 0

                    # 达到用户选择的数量后立即结束，绝不再执行后续空转次数。
                    if delivered >= target_quantity:
                        self.ui_queue.put((
                            "operation_done",
                            f"{MEDICINES[index]} medicine dispensing completed. Detected {delivered} pills"
                        ))
                        return

                    self.ui_queue.put((
                        "status",
                        f"{MEDICINES[index]} medicine. Detected {delivered}/{target_quantity} pills"
                    ))
                else:
                    consecutive_failed_positions += 1
                    if consecutive_failed_positions >= MAX_FAILED_POSITIONS:
                        # 停止前最后复核一次，避免边界时刻到达的信号被漏掉。
                        final_detected = self.get_active_drop_detected(index)
                        if final_detected > delivered:
                            delivered = final_detected
                            consecutive_failed_positions = 0
                            if delivered >= target_quantity:
                                self.ui_queue.put((
                                    "operation_done",
                                    f"{MEDICINES[index]} medicine dispensing completed. Detected {delivered} pills"
                                ))
                                return
                            continue

                        self.ui_queue.put((
                            "operation_warning",
                            f"{MEDICINES[index]} medicine: {MAX_FAILED_POSITIONS} consecutive positions had no pill detected. Task stopped. "
                            f"Detected {delivered}/{target_quantity} pills."
                        ))
                        return

            if not self.closing:
                self.ui_queue.put((
                    "operation_done",
                    f"{MEDICINES[index]} medicine dispensing completed. Detected {delivered} pills"
                ))
        except Exception as exc:
            self.ui_queue.put(("operation_error", f"Dispensing failed: {exc}"))
        finally:
            with self.data_lock:
                self.active_drop_index = None
                self.active_drop_detected = 0

    def advance_servo_position(self, index):
        position = self.servo_positions[index]
        direction = self.servo_directions[index]
        next_position = position + direction * 250

        if next_position >= 1000:
            next_position = 1000
            self.servo_directions[index] = -1
        elif next_position <= 0:
            next_position = 0
            self.servo_directions[index] = 1

        self.servo_positions[index] = next_position
        return next_position

    def begin_operation(self, status):
        if not self.operation_lock.acquire(blocking=False):
            return False
        self.busy = True
        self.set_controls_enabled(False)
        self.status_var.set(status)
        return True

    def finish_operation(self, status):
        self.busy = False
        if self.operation_lock.locked():
            self.operation_lock.release()
        self.set_controls_enabled(True)
        self.status_var.set(status)

    def set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.shake_button.configure(state=state)
        self.drop_button.configure(state=state)
        self.clear_button.configure(state=state)

    def change_medicine(self, step):
        if self.busy:
            return
        self.selected_medicine = (self.selected_medicine + step) % len(MEDICINES)
        self.medicine_var.set(MEDICINES[self.selected_medicine])

    def change_quantity(self, step):
        if self.busy:
            return
        self.selected_quantity = min(9, max(1, self.selected_quantity + step))
        self.quantity_var.set(str(self.selected_quantity))

    def schedule_total_save(self, index):
        if self.save_jobs[index] is not None:
            self.root.after_cancel(self.save_jobs[index])
        self.save_jobs[index] = self.root.after(350, lambda: self.commit_total(index))

    def commit_total(self, index):
        self.save_jobs[index] = None
        text = self.total_vars[index].get().strip()
        if text == "":
            return False
        try:
            value = int(text)
            if value < 0:
                raise ValueError
        except ValueError:
            with self.data_lock:
                old_value = self.records[index]["total"]
            self.total_vars[index].set(str(old_value))
            self.status_var.set("Total stock must be a non-negative integer")
            return False

        with self.data_lock:
            self.records[index]["total"] = value
            self.save_records_locked()
        self.refresh_remaining(index)
        self.status_var.set(f"{MEDICINES[index]} total stock saved as {value}")
        return True

    def commit_all_totals(self):
        for index in range(6):
            self.commit_total(index)

    def clear_all(self):
        if self.busy:
            return
        if not messagebox.askyesno("Confirm Clear", "Clear total stock and cumulative dispensed counts for all six medicines?"):
            return
        with self.data_lock:
            for record in self.records:
                record["total"] = 0
                record["dispensed"] = 0
            self.sensor_counts = [0] * 6
            self.save_records_locked()
        for i in range(6):
            self.total_vars[i].set("0")
        self.refresh_remaining()
        self.status_var.set("All medicine data has been cleared")

    def refresh_remaining(self, only_index=None):
        indices = range(6) if only_index is None else (only_index,)
        with self.data_lock:
            values = [self.records[i]["total"] - self.records[i]["dispensed"] for i in indices]
        for index, value in zip(indices, values):
            self.remaining_vars[index].set(str(max(value, 0)))

    def get_sensor_count(self, index):
        with self.data_lock:
            return self.sensor_counts[index]

    def get_active_drop_detected(self, index):
        with self.data_lock:
            if self.active_drop_index != index:
                return 0
            return self.active_drop_detected

    def load_records(self):
        default = [{"medicine": name, "total": 0, "dispensed": 0} for name in MEDICINES]
        if not CSV_PATH.exists():
            self.write_records(default)
            return default

        try:
            loaded = {}
            with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as file:
                for row in csv.DictReader(file):
                    name = row.get("medicine", "").strip().upper()
                    if name in MEDICINES:
                        loaded[name] = {
                            "medicine": name,
                            "total": max(0, int(row.get("total", 0))),
                            "dispensed": max(0, int(row.get("dispensed", 0))),
                        }
            records = [loaded.get(name, default[i]) for i, name in enumerate(MEDICINES)]
            self.write_records(records)
            return records
        except (OSError, ValueError, csv.Error):
            self.write_records(default)
            return default

    def write_records(self, records):
        temp_path = CSV_PATH.with_suffix(".csv.tmp")
        with temp_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=("medicine", "total", "dispensed"))
            writer.writeheader()
            writer.writerows(records)
        os.replace(temp_path, CSV_PATH)

    def save_records_locked(self):
        self.write_records(self.records)

    def process_ui_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "sensor":
                    index, actual = item[1], item[2]
                    self.refresh_remaining(index)
                    self.status_var.set(
                        f"Sensor detected {MEDICINES[index]} medicine. Cumulative dispensed: {actual} pills"
                    )
                elif kind == "status":
                    self.status_var.set(item[1])
                elif kind == "operation_done":
                    self.finish_operation(item[1])
                elif kind == "operation_warning":
                    self.finish_operation(item[1])
                    messagebox.showwarning("Dispensing Stopped", item[1])
                elif kind == "operation_error":
                    self.finish_operation(item[1])
                    messagebox.showerror("Operation Failed", item[1])
                elif kind == "shake_stopped":
                    if not self.shaking:
                        self.shake_button.config(text="Shake")
                        self.status_var.set("Shake stopped")

                elif kind == "shake_error":
                    self.shaking = False
                    self.shake_button.config(text="Shake")
                    self.status_var.set(item[1])
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(50, self.process_ui_queue)

    def on_close(self):
        if self.busy and not messagebox.askyesno("Exit", "The device is running. Stop and exit?"):
            return
        self.closing = True
        self.shaking = False
        self.shake_stop_event.set()
        try:
            if self.shake_thread is not None and self.shake_thread.is_alive():
                self.shake_thread.join(timeout=1.0)
            if self.motor_1 is not None:
                self.motor_1.stop()
                self.motor_1.close()
            if self.motor_2 is not None:
                self.motor_2.stop()
                self.motor_2.close()
            for sensor in self.sensors:
                sensor.close()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    MedicineUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
