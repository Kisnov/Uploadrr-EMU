from .chevereto import CheveretoTemplate


class IMGHOST(CheveretoTemplate):
    def __init__(self, config):
        super().__init__(config, "https://imghost.dev/api/1/upload", "imghost")
