from .chevereto import CheveretoTemplate

class PTSCREENS(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "ptscreens_api", "https://ptscreens.com/api/1/upload", "ptscreens")