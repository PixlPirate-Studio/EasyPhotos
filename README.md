# EasyPhotos

A lightweight, self-hosted photo gallery for Unraid. Like Immich or PhotoPrism, but smaller and simpler:
**one container, no separate database or AI containers.**

- Reads your **existing photo folders** (external libraries), never re-organises them
- Small, fast thumbnails (size set in Admin settings), iPhone **HEIC** and **Live Photos**
- **Face recognition** with the same InsightFace models Immich uses. Name a face once and every photo of that person is grouped
- Draw a box around a face it missed, fix wrong matches, or leave groups unmatched
- **Map** (offline place names), **Calendar** with "On this day", **Albums**, **Tags** (only the ones you add), **Favorites**, **Search**
- **Duplicate finder** (exact copies + look-alikes) that never deletes anything without asking
- **Recycle Bin**: deleted photos can be restored until you empty it
- **Users & roles**: Viewer (look only), User (look + download), Admin (everything). No sign-up; you add users and they choose their own password on first sign-in
- **Select many** → download as .zip, add to album, tag, fix dates, hide, delete
- **API** for other apps: random photos from folders you choose
- Encrypted single-file database in appdata
- Works on PC and phone (add it to your home screen)

Background work (thumbnails, faces, duplicates) runs at the lowest CPU and disk priority with a thread limit you choose,
so Jellyfin and your other apps always go first. It never stops, it just uses what's left over.

---

## Install on Unraid (new container, no Docker Compose)

1. **Docker → Add Container**, then fill in:

   | Field | Value |
   |---|---|
   | Name | `EasyPhotos` |
   | Repository | `ghcr.io/pixlpirate-studio/easyphotos:latest` |
   | WebUI | `http://[IP]:[PORT:8080]/` |
   | Icon URL | `https://raw.githubusercontent.com/PixlPirate-Studio/EasyPhotos/main/web/img/icon-512.png` |

2. Add these with **Add another Path, Port, Variable…**:

   | Type | Container | Host (example) | Notes |
   |---|---|---|---|
   | Port | `8080` | `8080` | The web page |
   | Path | `/config` | `/mnt/user/appdata/easyphotos` | Database + backups (small) |
   | Path | `/thumbs` | `/mnt/user/easyphotos-thumbs` | Thumbnails. Put on the **array**, not appdata |
   | Path | `/libraries/family` | `/mnt/user/Photos` | Your photos. **Read/Write** lets you delete to the Recycle Bin |
   | Path | `/libraries/server2` | `/mnt/disks/Server2/PHOTOS` | Another library (optional, add as many as you like) |
   | Variable | `EP_DB_KEY` | a long random password | Encrypts the database. Set it **before the first start** and never change it |
   | Variable | `TZ` | `America/Indiana/Indianapolis` | Your time zone |
   | Variable | `PUID` / `PGID` | `99` / `100` | Runs as nobody:users like other Unraid apps |

   (Or copy `unraid/easyphotos.xml` to `/boot/config/plugins/dockerMan/templates-user/` and pick it from the template list.)

3. **Apply**, then open the WebUI. The first page asks you to create the admin account.
4. Go to **Library → Folders & scanning → Add library** and pick `/libraries/family`.
   Thumbnails start right away. Click **Find faces** when you're ready. It can take a day or more on a big library.
5. Name people in **Library → Faces**.

### Updating
Every change pushed to this repo builds a new image automatically. In Unraid: **Docker → Check for Updates → Apply Update**.

> The first time GitHub builds the image, the package may be private. On GitHub go to
> **your profile/org → Packages → easyphotos → Package settings → Change visibility → Public**
> so Unraid can pull it without logging in.

---

## Users and roles

| Role | Can do |
|---|---|
| **Viewer** | Look at photos, people, places, calendar, tags, albums. No downloads. Can keep their own Favorites. |
| **User** | Everything a Viewer can, plus download single photos or a selection as a .zip |
| **Admin** | Everything: edit info, name faces, tags, albums, delete, settings, users, API keys |

There is no sign-up page. In **Admin → Users → Add user**, type a username and choose a role. The person goes to
the sign-in page, types that username, and chooses their own password. Admins can **reset a password** (the person
picks a new one next time) or **delete** the user. You can also limit which libraries each person can see.

---

## API (random photos for dashboards, photo frames, Home Assistant…)

Create a key in **Admin → API keys** and choose which folders it may use (several are fine; sub-folders included).
Send the key as the `X-API-Key` header or add `?key=…` to the address.

| Address | What you get |
|---|---|
| `GET /api/v1/random/image?key=KEY` | One random picture, as an image (good for an `<img>` tag) |
| `GET /api/v1/random?count=10&key=KEY` | Random photos as JSON with thumbnail/preview/original links |
| `GET /api/v1/photos/{id}/{thumbnail\|preview\|original}?key=KEY` | One photo |
| `GET /api/v1/info?key=KEY` | Key name and how many photos it can see |

Optional filters for the random endpoints: `size=thumbnail|preview|original`, `person=Sarah`, `tag=Dog`,
`album=Christmas`, `orientation=landscape|portrait`.

---

## Where things are stored

| What | Where |
|---|---|
| Database (names, faces, users, edits) | `/config/easyphotos.db` (encrypted when `EP_DB_KEY` is set) |
| Backups ("Back up now" in Settings) | `/config/backups/` (keeps the last 10) |
| Thumbnails, previews, face pictures | `/thumbs` (or a folder you choose in Settings) |
| Deleted photos | a hidden `.recycle` folder inside each library until you empty the Recycle Bin |
| Your originals | untouched. Edits (dates, places, descriptions, tags) are saved in EasyPhotos, optionally also as `.xmp` files |

## Credits
- Face models: [InsightFace](https://github.com/deepinsight/insightface) buffalo_l (non-commercial use)
- Place names: [GeoNames](https://www.geonames.org/) (CC BY 4.0)
- Maps: [Leaflet](https://leafletjs.com/) with © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
- Fonts: Inter and Space Grotesk (SIL Open Font License)
