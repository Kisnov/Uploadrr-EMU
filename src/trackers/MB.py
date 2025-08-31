from src.trackers.UNIT3D import Unit3d


class MB(Unit3d):
    tracker = "MB"
    source_flag = "MalayaBits"
    domain = "https://malayabits.cc"
    allowed_imghosts = []
    banned_groups = [""]
