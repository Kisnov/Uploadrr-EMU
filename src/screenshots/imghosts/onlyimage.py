from .chevereto import CheveretoTemplate


class ONLYIMAGE(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "onlyimage_api", "https://onlyimage.org/api/1/upload", "onlyimage")
