class DebugPrint:
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return f"Debug-Message: {self.message}"