from .chevereto import CheveretoTemplate


class LENSDUMP(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://lensdump.com/api/1/upload", "lensdump")
