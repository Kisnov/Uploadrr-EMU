from .chevereto import CheveretoTemplate


class HAMSTER(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://hamster.is/api/1/upload", "hamster")
