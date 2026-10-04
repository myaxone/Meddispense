import ServoControl
import time
if __name__ == '__main__': 
    while True:
        ServoControl.setBusServoMove(1, 0, 1000)
        time.sleep(2)
        ServoControl.setBusServoMove(1, 1000, 1000)
        time.sleep(2)
    