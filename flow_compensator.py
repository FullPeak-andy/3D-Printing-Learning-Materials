#!/usr/bin/env python3
"""
KLP1 动态流量补偿 - Moonraker 扩展
实时读温度 + 查 K 表 + 调流量偏移
仿拓竹 EEPROM K 表的运行时版本
"""

import requests
import time
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("flow_compensator")

MOONRAKER_URL = "http://127.0.0.1:7125"

# ========== 温度 K 表（4 耗材，基于拓竹/Klipper 论坛实测）==========
K_T_TABLES = {
    "PLA": {
        180: 0.92, 190: 0.96, 200: 1.00,
        210: 1.04, 220: 1.08, 230: 1.12
    },
    "PETG": {
        200: 0.93, 210: 0.96, 220: 1.00,
        230: 1.04, 240: 1.08, 250: 1.12
    },
    "ABS": {
        220: 0.92, 230: 0.95, 240: 1.00,
        250: 1.05, 260: 1.10, 270: 1.15
    },
    "TPU": {
        200: 0.95, 210: 0.98, 220: 1.00,
        230: 1.03, 240: 1.06
    }
}


def lookup_K_T(temperature: float, material: str = "PLA") -> float:
    """温度-流量补偿 K 表（线性插值）"""
    table = K_T_TABLES.get(material, K_T_TABLES["PLA"])
    temps = sorted(table.keys())

    if temperature <= temps[0]:
        return table[temps[0]]
    if temperature >= temps[-1]:
        return table[temps[-1]]

    for i in range(len(temps) - 1):
        t1, t2 = temps[i], temps[i + 1]
        if t1 <= temperature <= t2:
            k1, k2 = table[t1], table[t2]
            return k1 + (k2 - k1) * (temperature - t1) / (t2 - t1)


def lookup_K_v(velocity: float) -> float:
    """速度-流量补偿 K 表"""
    if velocity <= 200:
        return 1.00
    elif velocity <= 400:
        return 1.00 + (velocity - 200) * 0.00015
    elif velocity <= 600:
        return 1.03 + (velocity - 400) * 0.0003
    else:
        return 1.09 + (velocity - 600) * 0.0001


def get_extruder_temp() -> float:
    """读当前喷嘴温度"""
    try:
        r = requests.get(
            f"{MOONRAKER_URL}/printer/objects/query",
            params={"extruder": "temperature"},
            timeout=2
        )
        data = r.json()
        return data['result']['status']['extruder']['temperature']
    except Exception as e:
        log.error(f"读温度失败: {e}")
        return None


def get_print_speed() -> float:
    """读当前打印速度（gcode 中的 F 值）"""
    try:
        r = requests.get(
            f"{MOONRAKER_URL}/printer/objects/query",
            params={"toolhead": "max_velocity"},
            timeout=2
        )
        data = r.json()
        return data['result']['status']['toolhead']['max_velocity']
    except Exception as e:
        return 150.0


def get_current_material() -> str:
    """读当前耗材（从 _K_TABLE_VARS 变量）"""
    try:
        r = requests.get(
            f"{MOONRAKER_URL}/printer/objects/query",
            params={"gcode_macro _K_TABLE_VARS": "material"},
            timeout=2
        )
        data = r.json()
        return data['result']['status']["gcode_macro _K_TABLE_VARS"]['material'].strip('"')
    except Exception as e:
        return "PLA"


def send_flow_compensation(K_total: float, K_T: float, K_v: float, T: float, V: float):
    """发送 K 值到 Klipper（用 SET_GCODE_VARIABLE）"""
    try:
        script = (
            f"SET_GCODE_VARIABLE MACRO=_K_TABLE_VARS VARIABLE=K_total VALUE={K_total}\n"
            f"SET_GCODE_VARIABLE MACRO=_K_TABLE_VARS VARIABLE=K_T VALUE={K_T}\n"
            f"SET_GCODE_VARIABLE MACRO=_K_TABLE_VARS VARIABLE=K_v VALUE={K_v}\n"
            f"M117 K_T={K_T:.3f} K_v={K_v:.3f} T={T:.1f} K={K_total:.3f}"
        )
        requests.post(
            f"{MOONRAKER_URL}/printer/gcode/script",
            json={"script": script},
            timeout=2
        )
    except Exception as e:
        log.error(f"发送失败: {e}")


def main():
    log.info("=" * 60)
    log.info("KLP1 动态流量补偿启动")
    log.info("仿拓竹 EEPROM K 表：4 耗材温度 × 速度复合 K")
    log.info("=" * 60)
    log.info("K 表: PLA / PETG / ABS / TPU")
    log.info("轮询间隔: 2 秒")
    log.info("Ctrl+C 停止")
    log.info("=" * 60)

    while True:
        try:
            T = get_extruder_temp()
            V = get_print_speed()
            material = get_current_material()

            if T and V and T > 50:  # 仅在温度 > 50℃ 时才补偿
                K_T = lookup_K_T(T, material)
                K_v = lookup_K_v(V)
                K_total = K_T * K_v

                send_flow_compensation(K_total, K_T, K_v, T, V)
                log.info(
                    f"[{material}] T={T:.1f}°C V={V:.0f}mm/s "
                    f"K_T={K_T:.3f} K_v={K_v:.3f} K_total={K_total:.3f}"
                )

            time.sleep(2)
        except KeyboardInterrupt:
            log.info("用户停止")
            break
        except Exception as e:
            log.error(f"错误: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
