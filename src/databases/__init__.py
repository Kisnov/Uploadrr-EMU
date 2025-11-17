from .anilist import AniListHandler
from .deezer import DeezerHandler
from .discogs import DiscogsHandler
from .imdb import IMDbHandler
from .musicbrainz import MusicBrainzHandler
from .porndb import PornDbHandler
from .srrdb import is_scene
from .tmdb import TMDbHandler
from .tvmaze import search_tvmaze

__all__ = [
    "AniListHandler",
    "DeezerHandler",
    "DiscogsHandler",
    "IMDbHandler",
    "MusicBrainzHandler",
    "TMDbHandler",
    "is_scene",
    "search_tvmaze",
    "PornDbHandler",
]
