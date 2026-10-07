# Changelog

Every change worth knowing, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): until 1.0.0, a minor version may change how things work.

## [1.4.0] - 2026-10-07

### Added

- **Automatic backups.** Settings › Account › Back up the settings can save a backup every few days in the data
  folder's `backups` folder, the newest kept, each downloaded with the password. A new install can start from one:
  the setup offers Restore from a backup, from that folder or a file, with a new password. (#15)
- **Upgrades.** Activity › Upgrades finds the files a series' quality ladder says could be better, asks each
  service what it has (one episode at a time), and replaces them, even when Sonarr ranks both the same (H.264 and
  H.265 in 1080p). The Episodes tab marks them too. (#11)
- **Catch up.** Activity › Catch up lists the aired episodes your series still miss (those from before a series
  got its service, or that the automatic sync gave up on), within 7, 30 or 90 days or all, and downloads them
  together. (#12)
- **The right episode, checked first.** Before a download, the series' listing on its service is read (kept 15
  minutes): when another episode's title claims the number asked for, the episode is taken where its own title
  puts it, or not downloaded. A title that merely differs (no translation yet) changes nothing. (#12)
- **A language order per quality ladder**: which of the episode's audio and subtitle tracks to take (en-AU, then
  en), unless a series or its service sets `--a-lang` or `--s-lang`. And ladders move up and down. (#11)
- **A series TMDB links nowhere** is offered its channel's service (Channel 4 on ALL4, BBC on iP…) and looked up
  on it by name. (#12)
- **Every Unshackle server watched.** Each other server says in Settings whether it answers, and you are told
  when one goes down and when it is back. (#13)

### Fixed

- **Sonarr links work away from home.** A Sonarr reached by a LAN address or a Docker name is linked with the
  name the page was opened with (Tailscale, a VPN). Through a reverse proxy, the LAN link stays. (#14)
- **Episode links from TMDB lead to the series** for iPlayer, Channel 4, ITV, Channel 5, Paramount+, SBS and U;
  HBO Max's episode links give way to its series link. (#12)
- **A backup keeps the quality ladders** too.
- **Widescreen tracks fit their step.** A 1920x800 film or a 2:1 series counts as 1080p in a quality ladder,
  as Unshackle's own `--quality` takes it. (#11)
- **An Unshackle server that is down keeps its services**: they fail there, saying why, instead of going to
  another server with other cookies or proxies. (#13)
- **Another server's empty downloads folder** is the main server's, not Unshacklarr's own. (#13)
- **A link to one episode** (iPlayer's `/episode/`) suggests no season map, and Test this series says the link
  may be an episode. (#12)
- **Activity on a phone** no longer scrolls sideways by a few pixels.

### Changed

- **A series' settings, reordered.** Source, When, What to get, Unshackle options and Numbering, each a card with
  its purpose, a bar to jump between them, and a line that says where the series stands.
- **Clearer texts** throughout the page, the notifications and the errors, in every language.
- **A new version shows within the hour** in the header, instead of up to half a day later.
- **Cookies and CDM on a remote Unshackle** with no folder mounted say they are managed where it runs, not an
  error. The restart and log commands show only for the bundled compose's server. (#13)

## [1.3.2] - 2026-10-07

### Fixed

- **The resolution stays in the file name.** A file with no episode title after its number lost it on
  renaming (`S17E03.1080p` became `S17E03p`), and a title starting with a number lost that number
  (`S02E01.1000.Days`). Reported, with its cause, by mj23au (#9).

## [1.3.1] - 2026-10-06

### Fixed

- **A service that failed to load is told at once.** When unshackle serve could not import a service (a
  Python module missing where it runs), its download stops before it starts, saying which module, instead
  of failing later in serve's queue. Other services carry on. Needs an Unshackle after 5.4.0, which lists
  these failures; earlier ones work as before. By Artic0din (#5).
- **Closing Activity's output mid-download** no longer logs a connection error; the download goes on.
  By Artic0din (#4).

## [1.3.0] - 2026-10-06

### Added

**Downloads**
- **Accepted languages.** An episode with none of the audio languages accepted (fr, en), or without full
  subtitles in a required one, is not downloaded: it waits and is tried again, for every series or one.
- **The French dub once it comes.** An episode got in English is got again, in place, once the service has
  the language you prefer, checked once a day for a month.
- **Profiles to fall back on.** A refused login is tried again with another account before failing.
- **Ready for the release.** Three hours before an episode comes out, its series is checked on its service:
  a refused login, no CDM or a dead URL is told while there is time to fix it.
- **Room kept free.** Once set, nothing is downloaded while the downloads folder runs short, and you are told.
- **Quality ladders.** An ordered list of steps (a codec, a range, a height from… to…), tried against the
  episode's real tracks just before the download: the first step it has a track for sets the quality, codec
  and range; when none does, the episode fails with the tracks it has, and nothing outside the ladder comes.
  Made and ordered in Settings › Quality (three built in: 1080p, 4K then 1080p, Archival), picked for every
  series there, per service in Download options › Per service, or on a series' page (Off turns it off). (#8)
- **Download only.** An episode is downloaded, joined, named and checked, then waits in Activity › Waiting
  in downloads for you to import or delete it, never deleted on its own. For every series in Settings ›
  Automation › After the download, or on a series' page. (#6)
- **More than one Unshackle.** Other unshackle serve (one behind a VPN, say), each with its address, API
  key and downloads folder as it sees it, in Settings › Unshackle › Other Unshackle servers. A service
  downloads with the one picked for it in Download options › Per service; a service only one of them has
  goes to it. Stopping a download or answering its question reaches the right one. (#7)

**Series**
- **Find a series on its service** by name, instead of copying its URL.
- **Change several series at once**: their service, release time, languages or options.

**Everywhere**
- **Back up and restore the settings** from Account, the password and the session kept.
- **Sign in through a reverse proxy.** Authelia, Authentik or another proxy's sign-in opens Unshacklarr,
  from the proxy's own addresses only.

### Fixed

- **What's on the service?** shows as soon as a series gets its link, without reloading the page.

### Changed

- **A lighter sync.** The episodes to get come from the days the sync looks at, not from the whole
  library's gaps, and the Schedule opens faster.

## [1.2.3] - 2026-10-05

### Fixed

- **Suggestions under the services you have.** A streaming site maps to the code your Unshackle has for it
  (HBO Max as MAX or HMAX, Apple TV+ as ATV or ATVP), never to a service it does not have; links are put
  right by their site, whatever the code, and suggestions kept from before follow. Reported, with its
  first fix, by Artic0din.
- **Suggest services works again**: TMDB's site refused Unshacklarr as a fake browser; it now gives its own
  name. Reported by Artic0din.

## [1.2.2] - 2026-10-05

### Fixed

- **One card for failures in a row.** A release burst or the sync that fails an episode again adds to its
  last failed try, its attempts counted, instead of a card every 30 s; the same failure is notified once.
- **A release burst stops at a failure** that trying again would only repeat (refused, an error); it goes
  on only while the episode is not out yet, and the sync tries it again later.

## [1.2.1] - 2026-10-03

### Fixed

- **An anime's new episode is found by its absolute number** even when TMDB only has its Japanese
  title: the few Latin letters in it ("DE" in 修行DEディナー) no longer read as a title that the
  service's contradicts.

## [1.2.0] - 2026-10-03

An anime its service numbers from its first episode is found by Sonarr's absolute number.

### Added

- **Found by its absolute number.** A service that numbers a series from its first episode (Netflix's
  Ranma ½: Sonarr's S02E01, the 13th, is its S02E13; a single season on 6play) is matched by Sonarr's
  absolute number, on the series' page and in the sync, even with no title to go by. Only a number no
  other episode has there.

## [1.1.1] - 2026-10-03

### Fixed

- **A new release shows in the header.** The check asked GitHub's API, which allows 60 anonymous calls an
  hour per connection, all programs together: it was refused. It reads the release page now, and asks again
  an hour later when it gets no answer.

## [1.1.0] - 2026-10-03

An episode the service numbers its own way is found by its title, a retry asks what its attempt asked,
and a new release shows in the header.

### Added

- **Found by its title.** When the service has nothing under an episode's number, the sync looks for it
  by its title and downloads it under the service's own number (6play's single season, say), and says so.
- **A new release in sight.** The header shows when a newer Unshacklarr is out, its notes one click away.

### Fixed

- A retry asks the service for what its attempt asked, an episode found by its title included.

## [1.0.0] - 2026-09-30

The first release: jobs in Activity, what each service has, Settings redesigned, notifications
address by address, nine languages, an API for other programs, and a full security audit.

### Added

**Downloads**

- **Jobs.** Episodes picked together form one job in Activity: a card per episode, one shared
  output, and a summary at the end (time, size, average speed). Unfold the download going on, and
  the next one unfolds in its place when it ends. Each download's ⓘ tells its proxy, its CDM, where it runs
  and which attempt it is, with the same download as a command to copy.
- **Control over a job.** Pause it, stop it, cancel a queued episode or put it back, retry one or
  every failed one in a click: everything stays in the job.
- **Faster batches.** The next download starts while the previous one is renamed and imported.
- **Clearer output.** Each step is told, Unshackle's own log shows live, and you choose
  Essentials or Debug. A debug mode in Settings says even more.
- **In sight.** Queued and running episodes show in Activity (counted on its menu entry, from any
  page) and on their series' page, and
  can't be started twice. A download's tracks show how long each took. From the Schedule, an
  episode leads to its last download in Activity.
- **Episodes in parts** (Koh-Lanta on Molotov) can wait for every part before being imported.
- **A service's questions** (a code sent by e-mail, a PIN) are answered from Activity or the
  bell.

**Series**

- **What's on the service.** One click lists what the service has and marks each episode: on it
  or not, under another number, or better there than the file you have. Each season counts what
  can be caught up, and "Download" handles it all.
- **Matching by number and title.** An episode's number is checked against its title, and a title
  wins over a number: in Sonarr's words, TMDB's or TVDB's own in your language (no key), in the
  series' language, yours or English (a French Disney+ finds Futurama's episodes). An episode Sonarr
  calls "TBA" shows TVDB's title in your language. A season offset covers a service whose later seasons
  are shifted.
- **Change and download.** Set the numbering for one download only, with a live preview and the
  service's episodes picked by name.
- **Links** to the series on TVDB, TMDB, its service and Sonarr.
- **A pasted link picks its service**: paste a canalplus.com link, CanalPlus is chosen.
- **Its own broadcast schedule.** When Sonarr's dates are wrong (a channel ahead of TVDB), a series
  airs by its own: a first evening, a time, weekly days or daily, so many episodes an evening, and
  any evening changed on a calendar (a break, two the same night). The sync and the Schedule follow it.
- **CDM page, reworked.** What needs fixing first; services grouped by the device they use, each changed
  from a searchable picker; devices one line each (actions in a menu), the unused ones folded.
- **CDM choices.** "No CDM" for a service without DRM; remote CDMs added, changed and removed from the
  page, their key never shown; a test shows live, and "Test in use" checks only the devices services use.
- **No CDM, said.** A series whose service has no CDM of its own in unshackle.yaml says so, before
  a download too.
- **Its service's options in sight.** A series' settings show what Per service sets for its service;
  one to change for this series only is a click.

**Unshackle**

- **Edit `unshackle.yaml`** from Settings: highlighting, suggestions, checks before saving and
  the last ten versions kept.
- **Unshackle 5.5 options** such as `--remote` and `--server`, and those a remote server declares.

**Everywhere**

- **An API for other programs.** A script, Home Assistant or Shortcuts can check on Unshacklarr,
  start a sync or a download, stop one, answer a service's question and read the notifications,
  with a key from Settings › Account. See [docs/API.md](docs/API.md).
- **Discord notifications** are cards: coloured by what happened, with the series' poster, the
  service, the size and the time.
- **Notifications** open as a menu under the bell, grouped by job (else by day and series), with
  the full list one click away.
- **Languages.** English, French, Spanish, German, Brazilian Portuguese, Italian, Russian, Chinese and
  Japanese, picked in Settings or at the first run; notifications are sent in it too.
- **A first run in steps.** Welcome, Unshackle, Sonarr and Region, each checked before the next, the
  field in error pointed at; a country searched with its flag, the language picked in a corner, and a
  reload that picks up where it was.
- **Settings, redesigned.** Each section opens on where things stand (connected, next sync, cookies to
  renew, devices in use) with its actions at hand, then cards by subject with quick choices.
- **Notifications, address by address.** Add Discord, Telegram, ntfy, Pushover, e-mail or Gotify in a few
  fields, test each one, pick what each gets, preview a message and see what went out lately.
- **Quiet hours**: messages wait, then arrive as one summary.
- **More warnings**: cookies about to expire, a CDM device in use failing its daily test.
- **Log out other devices** from Account, the password kept.
- **Tooltips** on every button that only has an icon.
- **Large screens** use their whole width.
- **GitHub Actions** run the tests and publish an image at `ghcr.io/ownzzzz/unshacklarr`.

### Changed

- A release time counts from the day the episode airs: "the day it airs", "the day after", or up
  to two weeks before, for a platform ahead of the channel Sonarr follows; the series' settings
  show when its next episode will be tried.
- The app opens on the Schedule, whose days now list things in the order they happen; Settings
  comes last in the menu and the tab bar.
- The Terminal is now **Activity**, as in Sonarr: it shows downloads, jobs and their history more
  than a console. Old links still lead there.
- Save confirms on its button, filters show their counts, and examples in empty fields look like
  examples; a change put back as it was needs no saving.
- The late warning is set in Notifications, and 0 turns it off; Unshackle runs "Local" or "Remote".
- A new password is typed twice, its strength shown as you type; help texts read one sentence a line,
  and the TMDB key links to where it is made.
- Every file Unshacklarr writes is written safely, never half-way.
- The time zone is searched like the country, with its offset; addresses typed are checked before saving.
- A service with no CDM at all (none of its own, no default) is refused before downloading, with what to do; one set
  to No CDM (no DRM) still downloads.

### Fixed

- Activity no longer blinks while downloads run, and its output stays whole when a card unfolds.
- On an iPhone, the top of a series' page is no longer blurred.
- Two subtitles in one language (plain and SDH, or forced) each get their line: the track count adds up.
- A notification's text, an error's above all, can be selected and copied: messages stay while you do.
- A service numbering a series its own way no longer passes one episode for another: titles win over numbers,
  short ones (a country) included.
- The audio check recognises every language again, old three-letter tags included.
- A wrong current password, when changing it, no longer sends you to the login screen.
- **No episode downloaded twice.** A status Unshackle was slow to give no longer starts the same
  download again, and an episode a release burst just imported is no longer downloaded by the sync.
- **No half episode imported.** When a download fails after its first part, nothing is imported:
  the parts that came wait in the downloads folder.
- **Downloads that never end.** An unexpected error fails that one episode, said and notified,
  and the rest of the job carries on; the automatic syncs and checks survive one bad round.
- **Testing Unshackle in Settings** no longer restarts it under running downloads.
- **Numbering.** Episode tables typed as S1E6=S2E5 are understood, and an episode offset that
  leaves no episode on the service skips it instead of asking for an E00.
- **Series with their own schedule** still get the missing episodes from before it.
- **A proxy's password** no longer shows in the Schedule's commands.
- **The download dialog's preview** of episodes and file names shows again.
- **A faster, lighter page.** Nothing polls in a background tab, the Schedule never stalls the
  server on a slow NAS, and Escape closes only the dialog on top.
- **Every language complete.** About 300 texts that had stayed in English are translated, and
  counts read right in each language.
- **The Docker image** installs the exact versions the tests ran with.
- Small fixes: field styles, unused code removed, fewer calls to Sonarr.
- Activity no longer stops updating after Settings › CDM was opened.
- Leaving a series' page you only looked at no longer asks about unsaved changes.

### Security

A full audit found nothing critical nor high; what it found is fixed:

- **Logging in.** Wrong passwords sent all at once are all counted, and a burst can no longer
  saturate the server; logging out ends the session on the server too.
- **Secrets in the page.** Notification tokens and a proxy's password reach the browser masked; a
  remote CDM's secret never follows a new address; a new password turns off every push device.
- **Nothing from a CDN.** The terminal is served by Unshacklarr itself, and the page refuses to be
  framed by another site.
- **Shared folders.** A symbolic link planted in the downloads or the Unshackle config folder is
  never followed.
- **Hostile text.** A service's message can no longer freeze the server through the translations.
- **The API** cannot force a file to be replaced, and takes 100 episodes at a time at most.
- **Images and CI.** Every tool downloaded is pinned and checked against its SHA-256 sum.
- **Dependencies.** oauthlib 4.0.0, for two advisories on OAuth servers that never reached Unshacklarr
  (Apprise only uses it as a client).

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
