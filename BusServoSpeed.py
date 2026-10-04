import ServoControl
import time
if __name__ == '__main__': 
    while True:
        ServoControl.setBusServoMove(1, 0, 1200)
        time.sleep(2)
        ServoControl.setBusServoMove(1, 1000, 1200)
        time.sleep(2)
        ServoControl.setBusServoMove(1, 0, 400)
        time.sleep(1)
        ServoControl.setBusServoMove(1, 1000, 400)
        time.sleep(1)
    