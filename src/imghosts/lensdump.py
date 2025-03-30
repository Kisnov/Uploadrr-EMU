from .chevereto import CheveretoTemplate

class LENSDUMP(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "ptscreens_api", "https://lensdump.com/api/1/upload", "lensdump")