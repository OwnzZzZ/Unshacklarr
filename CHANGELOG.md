# Changelog

Every change worth knowing, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): until 1.0.0, a minor version may change how things work.

## [Unreleased]

Long jobs you can steer while they run, proxies that hold, and a security update of aiohttp: update soon if
Unshacklarr can be reached from outside your network.

### Security

- **aiohttp 3.14.4.** Eight advisories fixed, three of them high: the server no longer waits without end for a
  request's headers, can no longer be made to use too much memory, CPU or file descriptors, and parses requests more
  strictly. (#24)
- **certifi 2026.10.23.** The emSign root certificates, whose intermediate keys were compromised, are no longer
  trusted for Unshacklarr's own HTTPS connections (a critical advisory).

### Added

**📦 Long jobs**
- **A bar at the bottom of a job.** The episode going on stays in sight however long the job; one tap on the bar opens
  its output, a click beside folds it. On a phone, the job's buttons are in it too.
- **Show or hide a job's episodes.** Click Done, Queued, Failed… in a job to hide those episodes or show them again.
- **Options of a job.** While it runs, its download and service options can be changed for the episodes still to
  come, starting from the ones they have now; the series stays as it is.
- **Retry the failed episodes while the job runs**, and a retry goes before the episodes still to come.
- **A job survives a restart.** Its queued episodes are kept on disk and queued again when Unshacklarr starts; a paused
  job stays paused.
- **Delete the job.** A job that is over leaves the history in one click.
- **The video's codec in Activity.** Video 1080p SDR H.265: the codec asked while it downloads, the file's once made.

**🌐 Proxies**
- **A proxy that fails is tried again**, at once, 3 times, each on another server of the provider (nordvpn:fr). If it
  still fails, the job pauses and you are told, instead of failing every episode left.

**🎧 Services and languages**
- **orig in the accepted audio languages.** Each series' original language as Sonarr gives it, so "orig, fr" works for
  every series at once. In Settings › Download options.
- **Any service option.** A flag the service on a remote server knows, such as AMZN's `-B`, can be written as is in a
  service's or a series' options.
- **The API speaks the service's numbering.** `"numbering": "service"` takes Disney+'s S04E05 for the episode it is in
  Sonarr, and `"audio": "fr"` lets Unshacklarr decide: download, download again, or add the French track.

**🧩 Unshackle after 6.0.0**
- **A remote server's login questions.** An OTP or a PIN its login asks while it lists a series comes to the bell,
  with a field to answer.
- **What your Unshackle supports**, in Settings › Unshackle, each with what it needs; a series set to `--remote` on
  an Unshackle that cannot run it is told in the bell.
- **Its log, errors by service and remote sessions** in Settings › Unshackle, even in Docker, once `unshackle.yaml`
  has a `serve: dashboard: key:` and Unshacklarr sees that file.
- **Restart waits for the downloads**, with that key too: Restart in Settings › Unshackle says Unshackle is busy
  instead of cutting them.
- **`--generate-dv`.** With `-r HYBRID`, Dolby Vision made from the HDR10+ when a title has none. In Download options.
- **`--include-sample`**: a 60 second sample of each file, in a Sample folder next to it. In Download options.
- **Proxies by network or IP.** `nordvpn:fr@187.14.108.15`, `nordvpn:ca@-datacamp`: the API's `proxy` takes them.
- **Clear finished**, in Settings › Unshackle › Maintenance: Unshackle forgets its finished downloads.
- **The editor knows the new keys** of `unshackle.yaml`: `codec_names`, `muxing.track_names`, `original_label`…

**⏰ Release times**
- **Next episode.** Two episodes the same evening, published one after the other: the second is tried that many
  minutes later. In a series' When.

**📶 Quality ladders, simpler**
- **Ladders as chips.** A service or a series picks its ladder in one tap; Edit ladders leads to Settings, Quality.
- **What the ladder takes over is said.** `--quality`, `--vcodec` and `--vbitrate-range` set at that level are flagged
  with one button to remove them; with a ladder they are no longer applied, and the command shows the ladder's video.
- **A highest bitrate per step.** 1080p up to 6000 kbps, 4K at its best: a cap on one step only.

### Fixed

- **A service's failed login is no longer called a refused API key.** Serve's own reason is shown instead, so you
  don't look for a fault in a key that works. (#25, by mj23au)
- **Service options now reach a remote server.** `-B`, `--atmos` and the others were left out of a download through it.
- **Activity no longer freezes with a large job.** The episodes that don't change are no longer redrawn every second.
- **More episodes found by their title.** "TBC" counts as no title, like "TBA"; TVDB's "PAGE 14 Donjon" is Netflix's
  "Donjon"; a title only translated ("Dungeon", "Donjon") is no longer marked *Other title*.
- **Quality ladders with `--remote`.** A remote download reads the episode's tracks too, so its ladder and languages
  are checked before it starts instead of failing.
- **No false alarm before a premiere.** A new series or season with nothing out yet is no longer told to check its URL.
- **The output's scrollbar can be dragged.** The text covered it when the output was wider than its box.
- **Seasons on the service on a phone.** The field gets the full keyboard, so `=` and `,` can be typed.
- **`--remote` with the official Unshackle.** A download through a remote server and its options now go through
  Unshackle's own API (after 6.0.0); with 6.0.0, Per service says the options are not known and to write them as is.
- **A series downloaded through `--remote` is also listed and searched there.** Its episodes no longer start the
  service here, where it may not be set up (NF stopped with "Service NF exited").
- **Retries with Unshackle after 6.0.0.** A network or service hitch it now answers with 424, and a server about to
  restart, are tried again as before.
- **No more false cookie warnings.** Crave's load balancer cookies (a week) and Apple's `utsdc` (an hour) no longer
  count as the date the login ends.

**🆕 Sonarr 5, on this branch until it is out of beta**
- **Languages.** Your files without your audio languages, read from Sonarr 5's own tracks: watch them for the
  preferred audio, or download them again. In Activity.
- **Titles in your language.** A series whose titles Sonarr gives in English offers, on its page, to have them in
  yours: its episodes then match the service's better.
- **Episode order.** A series Sonarr numbers in another order (DVD, Netflix…) says so in its Numbering; when that
  order changes, its new episodes wait until you confirm the numbering.

### Removed

What only a fork of Unshackle gave: Unshacklarr now needs nothing but the official Unshackle.
- **The CDM devices' test, details and reprovisioning**, and the *A CDM device fails* alert. Adding, deleting and
  choosing a device stay.
- **The TV login code box.** Disney+ now shows its code as Unshackle's own notice on the download.
- **Unshackle's log in Activity**, and the proxy and CDM a download went through. With it, the output's
  Essentials/Debug switch: Settings' debug mode alone says how much the output shows.

## [1.5.0] - 2026-10-17

Several Sonarr at once, the audio of your language added to the files you have, Upgrades that run by themselves,
episodes matched far more reliably, and ready for Unshackle 6. Much of it came from mj23au's issues and code: thank you.

### Added

**📚 More than one Sonarr**
- **Other Sonarr instances.** A second Sonarr, a 4K one say, with its own library, quality ladder and After the
  download; its series are listed too, and its copy comes at the release time as well. In Settings › Sonarr. (#10)
- **Sonarr name.** Each Sonarr named; a badge on each series says which libraries have it, and Series filters by them. (#10)
- **Libraries per series.** In Source, each Sonarr switched on or off for the series, with what it misses. (#10)
- **Episodes, Catch up and Upgrades per Sonarr.** Pick the library, download for it. (#10)

**⬇️ Downloads**
- **Add audio track.** When your language's audio comes out, only that track is downloaded and added to your file, as
  its default audio, with its forced subtitles. A file not from the series' service is asked about in the bell first.
  In Settings › Download options.
- **Library folders picked, not typed.** Sonarr's root folders offered, both sides browsable, a real file to confirm them.
- **Fallback service.** A second service for a series, used when the main one doesn't have the episode.
- **Hybrid (DV + HDR10) in quality ladders.** A step can ask for both layers in one file.
- **Muxing progress.** Activity shows how far Unshackle's mux is and the time left, and folds the tracks once they
  are in. With Docker, mount its work folder (see the example compose).
- **Downloads at once.** How many episodes each Unshackle server downloads together; 1 suits a small NAS. (#10)
- **Download window.** Automatic downloads only between two times. In Settings › Automation.
- **Auto release times.** A series learns when its episodes come out. In Settings › Automation.

**🔎 Finding the right episode**
- **Separate programmes.** For strands like BBC Documentaries: each episode is found by its title, no series URL
  needed; one the service doesn't have says *Not on* it. (#17)
- **Seasons listed as separate shows.** A season the service lists as a show of its own downloads from that show's
  page, which Episodes offers when it finds one. (#21)

**⬆️ Upgrades**
- **Upgrades you choose.** Which series are checked, how long an answer is kept, how far back; one card per series,
  nothing ticked at first, each checkable on its own, replacements shown live. (#10, #18)
- **Check automatically.** Every 7 days, asking only about answers grown old, spread over several checks. In
  Settings › Upgrades. (#23)
- **Check new series.** A series set up with episodes already on disk is checked within the hour. (#23)
- **Other release groups.** A file from another group can be replaced by the same quality too. (#10)

**📡 Sonarr**
- **Season and series finales**, in the Schedule and in the notification.
- **Season names** in Episodes, where Sonarr has them.
- **Room in the library checked too.** Nothing downloads while the series' folder is short of space; you are told once.

**✨ Everywhere**
- **What's new.** Click the version in the header.
- **No spoilers.** Episode titles stay blurred until you click them. In Settings › Interface.
- **Notifications per series.** All, failures only, or none; the bell keeps everything.
- **Remote backup.** Your backups, encrypted, on WebDAV or S3. In Settings › Account.
- **A diagnostic for issues.** One click in Activity, without passwords, keys, addresses or folders.
- **A health check** at `/health`, for Docker and Uptime Kuma.
- **Sign-in codes.** When a service waits for you to sign in, you are told, with the link and the code when
  Unshacklarr runs Unshackle, and with Unshackle 6 its approval messages.
- **Dynamic range in Episodes.** DV, HDR10 or HDR10+ beside each file's codec.

**🆕 Unshackle 6**
- **Its proxies allowed in one click** to Unshacklarr's key, in Settings › Unshackle; on their own when Unshacklarr
  runs Unshackle. Unshackle 5.4 and later still work as before.
- **Its new download options**, each saying it needs 6.0, `--cdm` with the CDM devices you have.
- **A limit of requests** set on the key is waited for, not taken for a lost download.

New options are off until you turn them on, except Upgrades' automatic check (0 days turns it off).

### Changed

- **Faster and lighter.** One call per series for its episodes and files, title matching about three times quicker,
  fewer calls to Sonarr for series with their own schedule, and no repeated waits while a server is down.
- **One import at a time per Sonarr**, so big files don't reach it together. (#10)
- **A busy Unshackle server is waited for** 1, 3 then 10 minutes before a download fails. (#10)
- **Import needed**, with an Import button, instead of a failure when Sonarr didn't import. (#10)
- **Clearer selection in Series.** A check on each poster, the others dimmed.
- **Clearer choices in a series' settings.** Each choice shown once, the one it inherits marked default.
- **One schedule per series.** A series with its own broadcast schedule shows only that one.
- **More from the API.** A download for another Sonarr, as a retry or a replacement, through its own proxy, in the
  codec and height you name. (#10)
- **Updating Unshacklarr alone** (`--no-deps`, in the README and the example compose) leaves Unshackle and its
  downloads running.

### Fixed

**🔎 Finding the right episode**
- **Titles that differ a little are matched:** a typo, a two-part title, a short title at the end of a long one,
  specials the service numbers in its own seasons. (#12, #20)
- **Only the very title moves an episode** to another number: never a close one, a remake from other years, or
  another part. (#12, #17, #20)
- **No wrong season map offered** for a season the service doesn't have yet, or on a page without dates. (#20)
- **A season the numbering gives to another one** is left to it (Koh-Lanta).
- **A service in another language** is no longer taken for yours because a few words are alike.
- **Episode titles in your language again**, now read through Sonarr; a new season not translated yet is no longer
  mistaken for other episodes.
- **A separate programme** is found inside its series when the search only finds the series. (#17)
- **A series' link is found by its title** when the service only links an episode (HBO Max). (#12)
- **No "may be an episode" warning** for a series that is simply new. (#12)

**⬇️ Downloads and imports**
- **Missing subtitles no longer stop a download.** (#16)
- **No second download** of an episode Unshackle may still be fetching, retries included. (#10)
- **Imports are asked again** when Sonarr confirms late, refuses while busy, or says it imported without moving
  anything. (#10)
- **Retry works for another Sonarr's copy**, and Replace anyway holds through a retry or a stop. (#10)
- **An empty episode folder** no longer blocks the episode. (#10)
- **Pausing a waiting job** no longer blocks the downloads after it.
- **A series' own `--range`** (HYBRID) is kept when its quality ladder picks a track.

**⬆️ Upgrades**
- **Only real upgrades.** Episodes offers what Upgrades found, Hybrid DV files aren't listed, and the right episode
  is asked when the service numbers it apart. (#10, #12, #18)
- **A replaced episode leaves the list** for good. (#23)

**✨ Everywhere**
- **A series without a poster** shows its initials. (#22)
- **Activity no longer freezes** when you come back to it after a while in another tab.
- **The service check shows in Episodes** as soon as a suggested link is picked, without a reload.
- **An unreachable Sonarr is told once**, not at every sync.
- **A run that just ended** no longer shows "Interrupted" until a reload. (#17)
- **The Output panel** keeps its newest line in view. (#10)
- **H.266 (VVC) files** are named as such.

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
