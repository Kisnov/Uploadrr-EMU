from src.trackers.UNIT3D import Unit3d


class PTT(Unit3d):
    tracker = "PTT"
    source_flag = "PTT"
    domain = "https://polishtorrent.top"
    allowed_imghosts = []
    banned_groups = ["ViP", "BiRD", "M@RTiNU$", "inTGrity", "PSiG", "CiNEMAET", "MusicET", "TeamET", "R2D2"]
