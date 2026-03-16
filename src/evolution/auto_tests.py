import logging
import subprocess  # nosec B404 — run pytest for evolution, path from caller

class AutoTests:
    @staticmethod
    def run_tests(test_directory):
        try:
            result = subprocess.run(  # nosec B603 B607 — pytest by name, path from config
                ['pytest', test_directory], capture_output=True, text=True
            )
            
            if result.returncode != 0:
                logging.error("Tests failed!")
                logging.error(result.stdout)
                logging.error(result.stderr)
                return False
            logging.info("All tests passed!")
            return True
        except Exception as e:
            logging.error(f"Error running tests: {e}")
            return False