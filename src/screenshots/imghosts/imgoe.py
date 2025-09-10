from .chevereto import CheveretoTemplate


class IMGOE(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "imgoe_api", "https://onlyimage.org/api/1/upload", "imgoe")
