import ServoControl
import time

# 完整的往返位置序列
POSITIONS = [
    0,
    250,
    500,
    750,
    1000,
    750,
    500,
    250,
    0
]

MOVE_TIME = 1000   # 每次运动时间，单位ms
WAIT_TIME = 2      # 每个位置停留时间，单位s


if __name__ == '__main__':
    try:
        while True:
            for position in POSITIONS:
                servos = [
                    1, position,
                    2, position,
                    3, position,
                    4, position,
                    5, position,
                    6, position
                ]

                ServoControl.setMoreBusServoMove(
                    servos,
                    6,
                    MOVE_TIME
                )

                print(f"六个舵机到达位置：{position}")
                time.sleep(WAIT_TIME)

    except KeyboardInterrupt:
        print("\n测试停止")