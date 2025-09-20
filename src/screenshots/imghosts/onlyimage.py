from .chevereto import CheveretoTemplate


class ONLYIMAGE(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://onlyimage.org/api/1/upload", "onlyimage")
