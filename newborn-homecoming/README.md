# Welcome Home movie maker

Turns a folder of photos and raw video clips into one smooth 1080p MP4 for a projector:

- Opening title card (and an optional closing card) over a soft, blurred photo
- Slow zoom and pan on every photo, so stills don't look static
- Cross-fades between every shot, with a fade in from black and out to black
- Portrait phone photos and videos are shown whole on a blurred backdrop, with no black bars
- Shots are put in the order they were taken (from photo and video metadata)
- Optional background music: it's looped to fit, and the clips' own sound is lowered under it

## 1. Install (one time)

- **ffmpeg**: Mac `brew install ffmpeg` · Windows `winget install ffmpeg` · Linux `sudo apt install ffmpeg`
- **Python libraries**: `pip install pillow pillow-heif` (`pillow-heif` is only needed for iPhone `.HEIC` photos)

## 2. Run

Put all the photos and videos in one folder (subfolders are fine), then:

```bash
python3 make_movie.py ~/Desktop/homecoming \
  -o welcome_home.mp4 \
  --title "Welcome Home" \
  --subtitle "Baby Aria  ·  9 November 2026" \
  --ending "Our little family" \
  --music lullaby.mp3
```

## Useful options

| Option | What it does | Default |
|---|---|---|
| `--photo-seconds 6` | How long each photo stays on screen | 5 |
| `--max-clip-seconds 20` | Trims long raw clips to their middle 20s | full clip |
| `--transition 1.5` | Length of each cross-fade, in seconds | 1.0 |
| `--style dissolve` | Transition type: `fade`, `fadeblack`, `dissolve`, `smoothleft`, `circleopen`… | fade |
| `--clip-volume 0` | Mutes the clips' own sound (only the music plays) | 1.0, or 0.35 with music |
| `--music a.mp3 b.mp3` | Plays several songs one after another | none |
| `--fill crop` | Fills the whole screen (crops) instead of using the blurred backdrop | blur |
| `--order name` | Uses filename order instead of the date taken | date |
| `--width 3840 --height 2160` | Makes a 4K version | 1080p |

## Playing it on loop

Copy `welcome_home.mp4` to a USB stick and turn on **Repeat** in the projector's media player. You can also play it from a laptop in VLC with *Playback → Loop*. The movie fades in and out of black, so the loop point looks smooth.
