from src.trackers.UNIT3D import Unit3d


class OINK(Unit3d):
    tracker = "OINK"
    source_flag = "YOiNKED"
    domain = "https://yoinked.org"
    allowed_imghosts = []
    banned_groups = ["GalaxyRG", "LAMA", "MeGUSTA", "NAHOM", "RARBG", "YTS", "YiFY"]
