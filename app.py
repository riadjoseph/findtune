import re
import os
import math
import time
import urllib.parse

import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.exceptions import SpotifyException
import toml

# 1. PAGE CONFIG
st.set_page_config(page_title="🎵 TuneWeaver MVP", layout="wide")

# 2. LOAD CREDENTIALS

config_path = "config.toml"
if os.path.exists(config_path):
    config = toml.load(config_path)
    CLIENT_ID = config.get("spotify", {}).get("SPOTIPY_CLIENT_ID")
    CLIENT_SECRET = config.get("spotify", {}).get("SPOTIPY_CLIENT_SECRET")
else:
    CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
    CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    st.error("Spotify credentials not found—set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET.")
    st.stop()

# 3. INIT SPOTIFY CLIENT
@st.cache_resource
def get_spotify_client():
    try:
        manager = SpotifyClientCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
        return spotipy.Spotify(client_credentials_manager=manager)
    except Exception as e:
        st.error(f"Failed to initialize Spotify client: {e}")
        return None

sp = get_spotify_client()
if not sp:
    st.stop()

# 4. UTILITY FUNCTIONS

def safe_sp_call(fn, *args, **kwargs):
    """
    Wrap Spotify API calls to handle rate limits and API errors.
    """
    try:
        return fn(*args, **kwargs)
    except SpotifyException as e:
        status = e.http_status
        if status == 429:
            retry = int(e.headers.get('Retry-After', 1))
            time.sleep(retry)
            return safe_sp_call(fn, *args, **kwargs)
        elif status in (401, 403):
            st.error(f"Auth error ({status}): {e}")
        elif status == 404:
            return None
        else:
            st.warning(f"Spotify API error ({status}): {e}")
    except Exception as e:
        st.warning(f"Unexpected error: {e}")
    return None


def extract_artist_id(input_str: str) -> str:
    """
    Extract Spotify artist ID from a URI or URL, or return the input if it matches the ID pattern.
    """
    m = re.search(r'(?:spotify:artist:|open\.spotify\.com/artist/)([A-Za-z0-9]+)', input_str)
    if m:
        return m.group(1)
    # Fallback: if input is 22-char base62 ID
    if re.fullmatch(r'[A-Za-z0-9]{22}', input_str):
        return input_str
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def search_artist(name_or_id: str) -> dict | None:
    """
    Search by name or fetch directly by ID if given.
    """
    artist_id = extract_artist_id(name_or_id)
    if artist_id:
        return safe_sp_call(sp.artist, artist_id)
    # Name search
    res = safe_sp_call(sp.search, q=f"artist:{name_or_id}", type="artist", limit=1)
    if res and res.get('artists', {}).get('items'):
        return res['artists']['items'][0]
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_artist_details(ids: list[str]) -> list[dict]:
    """
    Batch-fetch artist details (up to 50 per request).
    """
    def chunked(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    details = []
    for chunk in chunked(ids, 50):
        res = safe_sp_call(sp.artists, chunk)
        if res and res.get('artists'):
            details.extend(res['artists'])
    return details


def get_related_artists(artist_id: str, num_artists=10, popularity_threshold=60) -> list[dict]:
    """
    Fetch related artists, filter by popularity, and return the least popular ones.
    """
    res = safe_sp_call(sp.artist_related_artists, artist_id)
    if not res or not res.get('artists'):
        return []
    ids = [a['id'] for a in res['artists']]
    artists = fetch_artist_details(ids)
    lesser = [a for a in artists if a.get('popularity', 100) < popularity_threshold]
    lesser.sort(key=lambda x: x.get('popularity', 100))
    return lesser[:num_artists]


def get_artist_top_tracks(artist_id: str, num_tracks=2, market='BE') -> list[dict]:
    """
    Return the top tracks for an artist in the specified market.
    """
    res = safe_sp_call(sp.artist_top_tracks, artist_id, market=market)
    return res.get('tracks', [])[:num_tracks] if res else []


def generate_streaming_links(track_name: str, artist_name: str) -> dict[str,str]:
    q = urllib.parse.quote_plus(f"{track_name} {artist_name}")
    return {
        "Spotify": f"https://open.spotify.com/search/{q}",
        "Apple Music": f"https://music.apple.com/us/search?term={q}",
        "Deezer": f"https://www.deezer.com/search/{q}",
        "YouTube Music": f"https://music.youtube.com/search?q={q}",
    }


def generate_playlist_search_link(tracks: list[dict]) -> dict[str,str]:
    """
    Create search URLs for the first few tracks of the playlist.
    """
    if not tracks:
        return {}
    parts = [f"{t['name']} {t['artist']}" for t in tracks[:4]]
    q = urllib.parse.quote_plus(", ".join(parts))
    return {
        "Spotify": f"https://open.spotify.com/search/{q}",
        "Apple Music": f"https://music.apple.com/us/search?term={q}",
        "Deezer": f"https://www.deezer.com/search/{q}",
    }

# 5. STREAMLIT UI
st.title("🎵 TuneWeaver MVP")
st.markdown("Discover lesser-known artists related to your favorites and weave a ~1-hour playlist.")

with st.form(key="tune_form"):
    seed_input = st.text_input("Enter artist name or URI/URL", placeholder="e.g. spotify:artist:1Xyo4u8uXC1ZmMpatF05PJ or The Weeknd")
    col1, col2 = st.columns(2)
    with col1:
        n_suggestions = st.slider("# of Artist Suggestions", 5, 20, 10)
    with col2:
        pop_thresh = st.slider("Max Popularity", 0, 100, 60,
                              help="Lower = more obscure artists")
    submit = st.form_submit_button("✨ Weave My Tune ✨")

if submit:
    if not seed_input.strip():
        st.warning("Please enter a seed artist.")
    else:
        with st.spinner("Building your playlist…"):
            seed_artist = search_artist(seed_input)
            if not seed_artist:
                st.error(f"No artist found for '{seed_input}'.")
            else:
                st.success(f"Seed: **{seed_artist['name']}** (Pop: {seed_artist.get('popularity')})")
                related = get_related_artists(seed_artist['id'], n_suggestions, pop_thresh)
                if not related:
                    st.warning("No lesser-known related artists found—try adjusting the threshold.")
                else:
                    st.subheader("Suggested Artists")
                    cols = st.columns(min(len(related), 5))
                    for i, art in enumerate(related):
                        with cols[i % len(cols)]:
                            if art.get('images'):
                                st.image(art['images'][-1]['url'], width=100)
                            genres = ", ".join(art.get('genres', [])[:3])
                            followers = art.get('followers', {}).get('total', 'N/A')
                            st.markdown(f"**{art['name']}** (Pop: {art.get('popularity')})\n\n"+
                                        f"Genres: {genres}\n\nFollowers: {followers}")
                            st.markdown(f"[Open on Spotify]({art['external_urls']['spotify']})")

                    # Build ~1-hour playlist
                    playlist, total_ms = [], 0
                    target_ms = 60*60*1000
                    # Add seed tracks
                    for t in get_artist_top_tracks(seed_artist['id'], 2):
                        if total_ms < target_ms:
                            playlist.append({"name": t['name'], "artist": seed_artist['name'], "duration_ms": t['duration_ms']})
                            total_ms += t['duration_ms']
                    # Add related tracks
                    for art in related:
                        if total_ms >= target_ms:
                            break
                        for t in get_artist_top_tracks(art['id'], 2):
                            if total_ms < target_ms:
                                playlist.append({"name": t['name'], "artist": art['name'], "duration_ms": t['duration_ms']})
                                total_ms += t['duration_ms']
                            else:
                                break
                    # Display playlist
                    if not playlist:
                        st.warning("Could not build a playlist—no tracks found.")
                    else:
                        mins = math.ceil(total_ms/60000)
                        st.subheader(f"🎶 Your Playlist (~{mins} minutes)")
                        for idx, tr in enumerate(playlist, 1):
                            mm, ss = divmod(tr['duration_ms']//1000, 60)
                            st.write(f"{idx}. **{tr['name']}** by {tr['artist']} ({mm:02d}:{ss:02d})")
                            links = generate_streaming_links(tr['name'], tr['artist'])
                            st.markdown(" | ".join(f"[{s}]({u})" for s, u in links.items()))

                        st.subheader("🔗 Search Full Playlist")
                        full_links = generate_playlist_search_link(playlist)
                        for svc, url in full_links.items():
                            st.markdown(f"- [{svc}]({url})")

st.markdown("---")
st.caption("Powered by Spotify Web API • MVP by TuneWeaver")
