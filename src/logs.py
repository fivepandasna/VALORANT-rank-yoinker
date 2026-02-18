import glob
import os
import time

LOGS_DIR = os.path.join(os.getenv('APPDATA'), 'vry', 'logs')

class Logging:
    def __init__(self):
        self.logFileOpened = False
        os.makedirs(LOGS_DIR, exist_ok=True)

    def log(self, log_string: str):
        log_files = glob.glob(os.path.join(LOGS_DIR, "log-*.txt"))

        log_file_numbers = [int(os.path.basename(file)[4:-4]) for file in log_files]
        
        if not log_file_numbers:
            log_file_numbers.append(0)
        
        log_file_name = os.path.join(LOGS_DIR, f"log-{max(log_file_numbers) + 1 if not self.logFileOpened else max(log_file_numbers)}.txt")

        with open(log_file_name, "a" if self.logFileOpened else "w") as log_file:
            self.logFileOpened = True
            current_time = time.strftime("%Y.%m.%d-%H.%M.%S", time.localtime(time.time()))
            log_file.write(f"[{current_time}] {log_string.encode('ascii', 'replace').decode()}\n")