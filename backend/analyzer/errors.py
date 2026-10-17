class AnalyzerError(RuntimeError):
    """Base error for callers of the synchronous analyzer."""


class VideoReadError(AnalyzerError):
    pass


class ModelNotConfiguredError(AnalyzerError):
    pass


class AnalysisTimeoutError(AnalyzerError):
    pass


class InferenceError(AnalyzerError):
    pass
