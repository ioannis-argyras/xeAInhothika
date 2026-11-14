# vendor: local font and icons

Served by the app itself, so the screens work without internet. Nothing here is edited except the
`src:` line of each Phosphor `style.css` (it points only to the local woff2 file).

| What | Version | Licence | Source URL |
|---|---|---|---|
| Ubuntu Sans (variable weight 400-700, subsets greek and latin) | Google Fonts v4 | SIL OFL 1.1 | https://fonts.googleapis.com/css2?family=Ubuntu+Sans:wght@400;500;700&display=swap , files from https://fonts.gstatic.com/s/ubuntusans/v4/ |
| Phosphor Icons web (regular, bold, fill) | 2.1.1 | MIT | https://unpkg.com/@phosphor-icons/web@2.1.1/src/{regular,bold,fill}/ |

The two Ubuntu Sans files are variable fonts: one file per subset covers weights 400, 500 and 700
(Google serves the same URL for all three).
`ubuntu-sans/OFL.txt` is a pointer, not the full text (see the note inside it).

## SHA-256

- `phosphor/LICENSE`: 81bb1bfd8e3730c19a3895ff4a82839980933504725960c52b1c207b29398f07
- `phosphor/Phosphor.woff2`: c2ea45ea05ff5c7df1936770c104725f2a68f43fd343f35f3da23a30b27de32a
- `phosphor/style.css`: 983e1c569506da3936a924de7002f733e992c9cfbdb3ec40ff60573a63fc1eb4
- `phosphor/bold/Phosphor-Bold.woff2`: 2153b52cea9e06fc0cc8496048be485b381a0f8bf130adea5a9756fc8f100b87
- `phosphor/bold/style.css`: 0dc1ebf1550e67f9c2dc048e0c02a9d0e3d21579eefffdb25a6f64ab2c388c3c
- `phosphor/fill/Phosphor-Fill.woff2`: 660bd6045c0e0d9756cddb8ba2ece3aad855df7d4a170ef23f7b1c0bf511c430
- `phosphor/fill/style.css`: f6429ccc3ae40d7d7c6018f7ade208525dcd4aa67d795790db39ece4a92fa692
- `ubuntu-sans/OFL.txt`: 5c090f7dbf1598a95085d900f1d9b763e24c854ed4f9d84a666497fa48483b3c
- `ubuntu-sans/ubuntu-sans-greek.woff2`: e248aa1e886aaaeb922be8c082a411a7b97440fa9e13c6976035dc790998a937
- `ubuntu-sans/ubuntu-sans-latin.woff2`: 8f34f6aeb6d02bae33e841c33848bfa689216f4323149d8471dc19c55ec8f2be
