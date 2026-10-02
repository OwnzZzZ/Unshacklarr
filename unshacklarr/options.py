"""unshackle's options, as the page shows them and as the API takes them.

The page and config.yaml name an option by its long flag (--a-lang), as on the command
line; unshackle serve takes it by name (a_lang) and typed (["fr"]). `dl` options come
from the table below: serve's /api/download accepts exactly these (DEFAULT_DOWNLOAD_PARAMS
in unshackle/core/api/handlers.py). A service's own options come from /api/services.
"""
import re
import shlex

# (name, kind, help, choices). Left out: what Unshacklarr sets itself (wanted, output_dir)
# or what makes no sense unattended (skip_dl, export, latest_episode).
DL_OPTIONS = [
    ('remote', 'flag', "Use a remote unshackle server from remote_services in unshackle.yaml for the service's own calls and its licences; the files still download here. Set it for one service in Per service.", []),
    ('server', 'text', 'Name of the remote server from remote_services, when several are configured. Only with --remote.', []),
    ('profile', 'text', 'Profile to use for Credentials and Cookies (if available).', []),
    ('quality', 'int_list', 'Download Resolution(s), defaults to the best available resolution.', []),
    ('vcodec', 'list', 'Video Codec(s) to download, defaults to any codec.', ['avc', 'H.264', 'hevc', 'H.265', 'vc1', 'VC-1', 'vp8', 'VP8', 'vp9', 'VP9', 'av1', 'AV1']),
    ('acodec', 'text', "Audio Codec(s) to download (comma-separated), e.g., 'AAC,EC3'. Defaults to any.", []),
    ('vbitrate', 'int', 'Video Bitrate to download (in kbps), defaults to highest available.', []),
    ('abitrate', 'int', 'Audio Bitrate to download (in kbps), defaults to highest available.', []),
    ('vbitrate_range', 'text', "Video Bitrate range in kbps (e.g., '6000-7000'). Selects the highest bitrate within the range.", []),
    ('abitrate_range', 'text', "Audio Bitrate range in kbps (e.g., '128-256'). Selects the highest bitrate within the range.", []),
    ('range', 'list', 'Video Color Range(s) to download, defaults to SDR.', ['SDR', 'HLG', 'HDR10', 'HDR10+', 'DV', 'HYBRID']),
    ('channels', 'number', 'Audio Channel(s) to download. Matches sub-channel layouts like 5.1 with 6.0 implicitly.', []),
    ('no_atmos', 'flag', 'Exclude Dolby Atmos audio tracks when selecting audio.', []),
    ('lang', 'list', "Language(s) wanted for Video and Audio (comma-separated). Use 'orig' to select the original language, e.g. 'orig,en' for both original and English. Prefix a value with '-' to exclude it, e.g. 'all,-es'.", []),
    ('v_lang', 'list', "Language wanted for Video. You would use this if the video language doesn't match the audio. Prefix a value with '-' to exclude it, e.g. 'all,-es'.", []),
    ('a_lang', 'list', "Language wanted for Audio, overrides -l/--lang for audio tracks. Prefix a value with '-' to exclude it, e.g. 'all,-es'.", []),
    ('s_lang', 'list', "Language wanted for Subtitles. Prefix a value with '-' to exclude it, e.g. 'all,-es'.", []),
    ('require_subs', 'list', 'Required subtitle languages. Downloads all subtitles only if these languages exist. Cannot be used with --s-lang.', []),
    ('forced_subs', 'flag', 'Include forced subtitle tracks.', []),
    ('require_audio', 'list', 'Audio languages that must exist. The title fails even with --best-available.', []),
    ('require_video', 'list', 'Video languages that must exist. The title fails even with --best-available.', []),
    ('forced_s_lang', 'list', "Languages wanted for forced subtitles, implies -fs. Keeps forced subs only in these languages. Prefix a value with '-' to exclude it, e.g. 'all,-es'.", []),
    ('exact_lang', 'flag', 'Use exact language matching (no variants). With this flag, -l es-419 matches ONLY es-419, not es-ES or other variants.', []),
    ('sub_format', 'text', "Set Output Subtitle Format, only converting if necessary. Use 'original' to keep source format.", ['subrip', 'srt', 'substationalpha', 'ssa', 'substationalphav4', 'ass', 'timedtextmarkuplang', 'ttml', 'webvtt', 'vtt', 'sami', 'smi', 'microdvd', 'sub', 'mpl2', 'tmp', 'fttml', 'stpp', 'fvtt', 'wvtt', 'original']),
    ('video_only', 'flag', 'Only download video tracks.', []),
    ('audio_only', 'flag', 'Only download audio tracks.', []),
    ('subs_only', 'flag', 'Only download subtitle tracks.', []),
    ('chapters_only', 'flag', 'Only download chapter markers.', []),
    ('no_subs', 'flag', 'Do not download subtitle tracks.', []),
    ('no_audio', 'flag', 'Do not download audio tracks.', []),
    ('no_chapters', 'flag', 'Do not download chapter markers.', []),
    ('no_video', 'flag', 'Do not download video tracks.', []),
    ('no_attachments', 'flag', 'Do not download or mux attachments (cover art, subtitle fonts, and files the service attaches).', []),
    ('audio_description', 'flag', 'Download audio description tracks.', []),
    ('slow', 'text', 'Add a delay between each Title download to act more like a real device. Use --slow for a 60-120s delay, or --slow MIN-MAX (e.g., --slow 20-40) for a custom range. Minimum delay is 20 seconds.', []),
    ('split_audio', 'text', 'Create separate output files per audio codec instead of merging all audio.', []),
    ('cdm_only', 'text', 'Only use CDM, or only use Key Vaults for retrieval of Decryption Keys.', []),
    ('proxy', 'text', 'Proxy URI to use. If a 2-letter country is provided, it will try to get a proxy from the config.', []),
    ('no_proxy', 'flag', 'Force disable all proxy use.', []),
    ('proxy_download', 'text', 'Proxy for the downloads only, in the same form as --proxy. Manifest, license, and auth use --proxy.', []),
    ('no_proxy_download', 'flag', 'Bypass proxy for all downloads. Manifest, license, and auth still use proxy.', []),
    ('no_folder', 'flag', 'Disable folder creation for TV Shows.', []),
    ('no_source', 'flag', 'Disable the source tag from the output file name and path.', []),
    ('no_mux', 'flag', 'Do not mux tracks into a container file.', []),
    ('workers', 'int', 'Max workers/threads to download with per-track. Default depends on the downloader.', []),
    ('adaptive_workers', 'flag', 'Scale per-track segment workers up to the --workers cap from measured CDN throughput and errors.', []),
    ('download_processes', 'int', "Split a track's segment downloads across this many processes; only engages for large segment batches (default 1).", []),
    ('continue_downloads', 'flag', 'Keep completed segment files across runs and resume a previously failed download.', []),
    ('downloads', 'int', 'Amount of tracks to download concurrently.', []),
    ('worst', 'flag', 'Select the lowest bitrate track within the specified quality. Requires -q/--quality.', []),
    ('best_available', 'flag', 'Continue with best available quality if requested resolutions are not available.', []),
    ('repack', 'flag', 'Add REPACK tag to the output filename.', []),
    ('tag', 'text', 'Set the Group Tag to be used, overriding the config tag and any tag rules.', []),
    ('tmdb_id', 'int', 'Use this TMDB ID for tagging instead of an automatic search. Add --enrich to also take its title, year and original language.', []),
    ('imdb_id', 'text', 'Use this IMDB ID (e.g. tt1375666) for tagging instead of an automatic search. Add --enrich to also take its title, year and original language.', []),
    ('tvdb_id', 'int', 'Use this TVDB ID for --tvdb-order and tagging instead of looking the series up automatically. Add --enrich to also take its title, year and original language.', []),
    ('tvdb_order', 'text', "Renumber episodes to a TVDB season order. Services normally use 'official' (aired) order; a series' Streaming Order is 'alternate'. Defaults to the tvdb_order config option.", ['official', 'dvd', 'absolute', 'alternate', 'regional']),
    ('anilist_id', 'text', 'Use this AniList ID for tagging instead of an automatic search, or a MyAnimeList ID as mal:12345. Add --enrich to also take its title, year and original language. Combines with one of --tmdb, --imdb or --tvdb, which AniList does not know.', []),
    ('enrich', 'flag', "Overwrite the show title, year and original language with the external source's. Requires --tmdb, --imdb, --tvdb, or --anilist.", []),
    ('daily', 'flag', 'Treat the title as daily/date-based content and fill missing air dates from TVDB during --enrich.', []),
    ('no_cache', 'flag', 'Bypass title cache for this download.', []),
    ('reset_cache', 'flag', 'Clear title cache before fetching.', []),
]

SERVICE_TYPES = {"boolean": "flag", "integer": "int", "float": "number"}  # click's type names; the rest is text


# Where Unshackle's long flag is not the name with dashes.
FLAGS = {"no_atmos": "--noatmos", "tmdb_id": "--tmdb", "imdb_id": "--imdb", "tvdb_id": "--tvdb", "anilist_id": "--anilist"}
NAMES = {flag: name for name, flag in FLAGS.items()}


def flag_of(name: str) -> str:
    return FLAGS.get(name) or "--" + name.replace("_", "-")


def name_of(flag: str) -> str:
    return NAMES.get(flag) or flag.lstrip("-").replace("-", "_")


def dl_specs() -> list[dict]:
    return [{"flag": flag_of(n), "short": "", "help": h, "is_flag": k == "flag", "choices": c, "kind": k}
            for n, k, h, c in DL_OPTIONS]


def service_specs(service: dict) -> list[dict]:
    """A service's own options, from the cli_params /api/services gives."""
    specs = []
    for p in service.get("cli_params") or []:
        if p.get("kind") != "option" or not p.get("name"):
            continue
        kind = "flag" if p.get("is_flag") else SERVICE_TYPES.get(p.get("type"), "text")
        if p.get("multiple"):
            kind = "list"
        opts = p.get("opts") or [flag_of(p["name"])]
        specs.append({"flag": max(opts, key=len), "short": min(opts, key=len), "help": p.get("help") or "",
                      "is_flag": kind == "flag", "choices": [str(c) for c in p.get("choices") or []], "kind": kind,
                      "name": p["name"]})
    return specs


def convert(value, kind: str):
    """A value as typed in the page (text, or true for a flag) into what the API expects."""
    if kind == "flag":
        return bool(value)
    text = str(value).strip()
    if kind == "int":
        return int(text)
    if kind == "number":
        return float(text)
    if kind in ("list", "int_list"):
        items = [v.strip() for v in text.split(",") if v.strip()]
        return [int(v.rstrip("pP")) for v in items] if kind == "int_list" else items
    return text


def from_dl_config(dl: dict, service: str) -> dict:
    """The `dl:` section of unshackle.yaml as `unshackle dl` applies it on the command line
    (its values, then those under the service's tag), which serve's API does not: typed as
    the API wants them. Keys the API does not take (e.g. `best`) are left out."""
    kinds = {name: kind for name, kind, _, _ in DL_OPTIONS}
    merged = {**{k: v for k, v in dl.items() if not isinstance(v, dict)}, **(dl.get(service) or {})}
    params = {}
    for name, value in merged.items():
        if name not in kinds or value is None or value == "" or value == []:
            continue
        kind = kinds[name]
        if isinstance(value, list):
            value = ",".join(map(str, value)) if kind in ("list", "int_list") else value
        try:
            params[name] = convert(value, kind) if not isinstance(value, bool) or kind == "flag" else value
        except ValueError:
            continue  # a value the command line would reject too: not ours to fix
    return params


def to_params(options: dict, specs: list[dict]) -> dict:
    """{--flag: value} to {name: typed value}; an empty value or a flag set to false is left out."""
    by_flag = {s["flag"]: s for s in specs}
    params = {}
    for flag, value in options.items():
        if value in (None, False, ""):
            continue
        spec = by_flag.get(flag) or {"kind": "flag" if value is True else "text"}
        try:
            params[spec.get("name") or name_of(flag)] = convert(value, spec["kind"])
        except ValueError:
            raise ValueError(f"{flag} needs a number, not {value!r}") from None
    return params


HIDDEN = re.compile(r"//[^@/\s]+@")  # a proxy's user:password


def command_line(request: dict) -> str:
    """A download as `unshackle dl` runs it by hand: dl's options, the service and its own, the title, the
    episode. A proxy's credentials are masked: the line is shown, and copied from the page."""
    dl_names = {name for name, *_ in DL_OPTIONS}

    def args(name: str, value) -> list[str]:
        if isinstance(value, bool):
            return [flag_of(name)] if value else []
        value = ",".join(map(str, value)) if isinstance(value, list) else str(value)
        return [flag_of(name), HIDDEN.sub("//***@", value)]

    own = [a for k, v in request.items() if k not in dl_names and k not in ("service", "title_id", "wanted", "output_dir", "debug")
           for a in args(k, v)]
    return shlex.join(["unshackle", "dl", *[a for k, v in request.items() if k in dl_names for a in args(k, v)],
                       request["service"], *own, str(request["title_id"]), "-w", ",".join(request["wanted"])])
