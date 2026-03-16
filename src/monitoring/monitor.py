class Monitor:
    def __init__(self):
        self.metrics = {
            'calls': 0,
            'errors': 0,
            'successes': 0,
        }

    def log_call(self):
        self.metrics['calls'] += 1

    def log_error(self):
        self.metrics['errors'] += 1

    def log_success(self):
        self.metrics['successes'] += 1

    def report(self):
        return self.metrics
