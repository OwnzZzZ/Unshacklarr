# Changelog

Every change worth knowing, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): until 1.0.0, a minor version may change how things work.

## [Unreleased]

## [1.5.0] - 2026-10-09

### Added

**More than one Sonarr**
- **Other Sonarr instances.** A second Sonarr with its own library, its own quality ladder and After the download.
  In Settings › Sonarr. (#10, with mj23au)
- **Libraries per series.** In Source, each Sonarr that has the series, switched on or off, with what it misses. (#10)
- **Series from every Sonarr.** A series only in another Sonarr is listed too; a badge names its libraries, and the
  Series list filters by them. (#10)
- **Sonarr name.** The main Sonarr can be named, like the others.
- **Episodes, Catch up and Upgrades per Sonarr.** Pick the library, download for it.

**Downloads**
- **Add audio track.** Get only the new audio, not the whole episode again. In Settings › Download options.
- **Fallback service.** A second service for a series, used when the main one doesn't have the episode.
- **Download window.** Automatic downloads run only between two times. In Settings › Automation.
- **Auto release times.** A series gets the time its episodes usually come out. In Settings › Automation.
- **Downloads at once.** How many episodes each Unshackle server downloads together, 1 for a small NAS. (#10)

**Upgrades**
- **Upgrades you choose.** Which series are checked, how long an answer is kept, how far back. (#10, by mj23au)
- **Other release groups.** A file from another group is replaced by the same quality too. (#10, by mj23au)

**Everywhere**
- **What's new.** Click the version in the header to see what changed.
- **No spoilers.** Episode titles stay blurred until you click them. In Settings › Interface.
- **Notifications per series.** All, failures only, or none. The bell keeps everything.
- **Remote backup.** Your backups, encrypted, on WebDAV or S3. In Settings › Account.
- **A diagnostic to attach to an issue**, without passwords, keys or tokens. In Activity.
- **A health check.** `/health` for Docker and Uptime Kuma.
- **Sign-in codes.** A service slow to answer may be waiting for you to sign in: you are told. With Unshackle run
  by Unshacklarr, a popup gives the link and the code.

The new options are off until you turn them on.

### Changed

- **One import at a time per Sonarr:** big files no longer reach Sonarr together. (#10, by mj23au)
- **A slow Unshackle server is waited for** 1, 3 then 10 minutes before a download fails. (#10, by mj23au)
- **Clearer selection in Series:** a check on each poster, the others dimmed.

### Fixed

- **Missing subtitles no longer stop a download:** it comes with the languages it has. (#16, by mj23au)
- **An import Sonarr confirms late** is no longer failed. (#10, by mj23au)
- **No second download** of an episode Unshackle may still be fetching. (#10, by mj23au)
- **Retry works for another Sonarr's copy.** (#10, by mj23au)
- **Hybrid Dolby Vision files** are no longer listed as upgrades. (#10, by mj23au)
- **A series' link found by its title** when the service only links an episode (HBO Max). (#12, by mj23au)
- **No more "may be an episode" warning** for a series that is simply new. (#12, reported by mj23au)

## [1.4.0] - 2026-10-07

### Added

- **Automatic backups.** Your settings saved every few days, and restored on a new install. In Settings › Account. (#15)
- **Upgrades.** Replace files your quality ladder says could be better. In Activity › Upgrades. (#11)
- **Catch up.** Download the aired episodes your series still miss, all at once. In Activity › Catch up. (#12)
- **The right episode, checked first.** No download when the service's title says it's another episode. (#12)
- **A language order per quality ladder**, for audio and subtitle tracks (en-AU, then en). (#11)
- **Services for series without a link**, found from their channel (Channel 4, BBC…). (#12)
- **Every Unshackle server watched.** You are told when one goes down and when it is back. (#13)

### Fixed

- **Sonarr links work away from home**, through Tailscale or a VPN. (#14)
- **TMDB links lead to the series**, not to one episode, for iPlayer, Channel 4, ITV and others. (#12)
- **Backups keep the quality ladders.**
- **Widescreen tracks fit their step**: a 1920x800 video counts as 1080p. (#11)
- **An Unshackle server that is down** no longer sends its services to another server. (#13)
- **Another server's downloads folder** is found when left empty. (#13)
- **A link to one episode** is spotted when you test a series. (#12)
- **Activity on a phone** no longer scrolls sideways.

### Changed

- **A series' settings, reordered** into cards, with a bar to jump between them.
- **Clearer texts** throughout, in every language.
- **A new version shows within the hour**, instead of half a day.
- **Cookies and CDM on a remote Unshackle** are said to be managed there, not shown as an error. (#13)

## [1.3.2] - 2026-10-07

### Fixed

- **The resolution stays in the file name** after renaming. (#9, reported by mj23au)

## [1.3.1] - 2026-10-06

### Fixed

- **A service that failed to load** stops its download at once, saying why. (#5, by Artic0din)
- **Closing Activity during a download** no longer logs an error. (#4, by Artic0din)

## [1.3.0] - 2026-10-06

### Added

- **Accepted languages.** An episode without the audio or subtitles you need waits and is tried again.
- **The French dub once it comes.** An episode is downloaded again when your preferred language arrives.
- **Profiles to fall back on.** A refused login is tried again with another account.
- **Ready for the release.** A series is checked 3 hours before its episode, while there is time to fix it.
- **Room kept free.** Nothing is downloaded while the disk runs short, and you are told.
- **Quality ladders.** Choose the quality, codec and range to try, in order. In Settings › Quality. (#8)
- **Download only.** Episodes wait in Activity for you to import them. (#6)
- **More than one Unshackle**, one behind a VPN for example. In Settings › Unshackle. (#7)
- **Find a series on its service** by name, instead of copying its URL.
- **Change several series at once.**
- **Back up and restore the settings** from Account.
- **Sign in through a reverse proxy** (Authelia, Authentik).

### Fixed

- **What's on the service?** shows as soon as a series gets its link.

### Changed

- **A lighter sync**, and a faster Schedule.

## [1.2.3] - 2026-10-05

### Fixed

- **Suggested services** match the ones your Unshackle has (MAX or HMAX, ATV or ATVP). (by Artic0din)
- **Suggest services works again** with TMDB. (by Artic0din)

## [1.2.2] - 2026-10-05

### Fixed

- **One card for repeated failures**, and one notification.
- **A release burst stops** when trying again would fail the same way.

## [1.2.1] - 2026-10-03

### Fixed

- **An anime's new episode is found** even when TMDB only has its Japanese title.

## [1.2.0] - 2026-10-03

### Added

- **Found by its absolute number**, for a service that numbers a series from its first episode.

## [1.1.1] - 2026-10-03

### Fixed

- **A new release shows in the header** again: GitHub refused the check.

## [1.1.0] - 2026-10-03

### Added

- **Found by its title** when the service numbers an episode differently.
- **A new release shows in the header**, its notes one click away.

### Fixed

- **A retry asks for the same episode** as the first try.

## [1.0.0] - 2026-09-30

### Added

- **Jobs.** Episodes picked together form one job in Activity, with a summary at the end.
- **Control over a job**: pause, stop, cancel or retry, one episode or all.
- **Faster batches.** The next download starts while the previous one is imported.
- **Clearer output**, with Unshackle's own log live.
- **Downloads in sight** in Activity and on the series page, never started twice.
- **Episodes in parts** wait for every part before being imported.
- **A service's questions** (a code by e-mail, a PIN) answered from Activity.
- **What's on the service.** See what the service has for each episode, and download what's missing.
- **Matching by number and title**, in Sonarr's, TMDB's or TVDB's words.
- **Change the numbering for one download**, with a live preview.
- **Links** to the series on TVDB, TMDB, its service and Sonarr.
- **A pasted link picks its service.**
- **A series' own broadcast schedule**, when Sonarr's dates are wrong.
- **The CDM page, reworked**: what needs fixing first, devices one per line.
- **CDM choices**: no CDM, remote CDMs, live tests.
- **A series without a CDM** says so before downloading.
- **Its service's options** shown in a series' settings.
- **Edit `unshackle.yaml`** from Settings, with the last ten versions kept.
- **Unshackle 5.5 options**, such as `--remote` and `--server`.
- **An API for other programs**: Home Assistant, Shortcuts or a script.
- **Discord notifications** as cards, with the poster.
- **Notifications** under the bell, grouped by job.
- **Nine languages**, notifications included.
- **A first run in steps**, each checked before the next.
- **Settings, redesigned**, each section opening on where things stand.
- **Notifications by address**: Discord, Telegram, ntfy, Pushover, e-mail or Gotify.
- **Quiet hours**: messages wait, then arrive as one summary.
- **More warnings**: cookies about to expire, a CDM failing its test.
- **Log out other devices** from Account.
- **Tooltips** on every icon button.
- **Large screens** use their whole width.
- **A Docker image on GitHub** at `ghcr.io/ownzzzz/unshacklarr`.

### Changed

- **Release times** count from the day the episode airs.
- **The app opens on the Schedule**, and Settings comes last.
- **The Terminal is now Activity.**
- **Save confirms on its button**, and filters show their counts.
- **A new password is typed twice**, with its strength shown.
- **Files are always written whole**, never half-way.
- **The time zone is searched** like the country.
- **A service without any CDM** is refused before downloading.

### Fixed

- **Activity no longer blinks** during downloads.
- **The top of a series' page** is no longer blurred on iPhone.
- **Two subtitles in one language** are both counted.
- **Notifications can be copied.**
- **One episode is no longer taken for another** when a service numbers its own way.
- **The audio check** recognises every language again.
- **A wrong current password** no longer logs you out.
- **No episode downloaded twice.**
- **No half episode imported.**
- **Downloads that never end** now fail with a reason, and the job goes on.
- **Testing Unshackle** no longer restarts it during downloads.
- **Numbering** typed as S1E6=S2E5 is understood.
- **Series with their own schedule** still get their earlier missing episodes.
- **A proxy's password** no longer shows in commands.
- **The download preview** shows again.
- **A faster, lighter page.**
- **Every language complete.**
- **The Docker image** uses the exact tested versions.
- **Activity keeps updating** after the CDM page was opened.
- **Leaving a series' page** without changes no longer asks to save.

### Security

- **Logging in** resists bursts of wrong passwords, and logging out ends the session.
- **Secrets** reach the browser masked.
- **Nothing from a CDN**, and the page can't be framed by another site.
- **Symbolic links** in shared folders are never followed.
- **A service's message** can no longer freeze the server.
- **The API** takes 100 episodes at a time at most.
- **Every tool in the images** is pinned and checked.
- **oauthlib 4.0.0**, for two advisories.

[1.5.0]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.5.0
[1.4.0]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.4.0
[1.3.2]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.3.2
[1.3.1]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.3.1
[1.3.0]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.3.0
[1.2.3]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.2.3
[1.2.2]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.2.2
[1.2.1]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.2.1
[1.2.0]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.2.0
[1.1.1]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.1.1
[1.1.0]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.1.0
[1.0.0]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.0.0
