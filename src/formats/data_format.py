class Task:
    def __init__(self, task_id, description, priority):
        self.task_id = task_id
        self.description = description
        self.priority = priority

class AnalysisResult:
    def __init__(self, success, details):
        self.success = success
        self.details = details

class Metrics:
    def __init__(self, success_rate, patch_quality, test_pass_ratio):
        self.success_rate = success_rate
        self.patch_quality = patch_quality
        self.test_pass_ratio = test_pass_ratio

class Report:
    def __init__(self, test_results, analysis_results, metrics):
        self.test_results = test_results
        self.analysis_results = analysis_results
        self.metrics = metrics