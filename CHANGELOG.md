# Changelog

Every change worth knowing, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): until 1.0.0, a minor version may change how things work.

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

[1.0.0]: https://github.com/OwnzZzZ/Unshacklarr/releases/tag/v1.0.0
