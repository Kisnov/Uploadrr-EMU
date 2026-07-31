from .chevereto import CheveretoTemplate


class IMGBB(CheveretoTemplate):
    def __init__(self, config):
        # key_in_query: imgbb's API v1 takes the key ONLY as ?key=, never as a
        # header (see https://api.imgbb.com/). Without this every imgbb upload
        # failed with "Invalid API v1 key" even with a perfectly valid key.
        super().__init__(config, "https://api.imgbb.com/1/upload", "imgbb", key_in_query=True)
