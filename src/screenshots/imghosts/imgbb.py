from .chevereto import CheveretoTemplate


class IMGBB(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "imgbb_api", "https://api.imgbb.com/1/upload", "imgbb")
