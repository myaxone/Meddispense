import ServoControl
import time
if __name__ == '__main__': 
    while True:
        servos = [1, 1000, 2, 0]
        ServoControl.setMoreBusServoMove(servos, 2, 1000)
        time.sleep(2)
        servos = [1, 0, 2, 1000]
        ServoControl.setMoreBusServoMove(servos, 2, 1000)
        time.sleep(2)
    