import time

import ServoControl


MOVE_TIME = 2000


if __name__ == "__main__":
    servos = [
        1, 0,
        2, 0,
        3, 0,
        4, 0,
        5, 0,
        6, 0,
    ]

    ServoControl.setMoreBusServoMove(servos, 6, MOVE_TIME)
    print("六个舵机正在回到 0 position，用时 2000 ms")
    time.sleep(MOVE_TIME / 1000.0)
    print("六个舵机已回到 0 position")
