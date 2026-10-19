# Vendored libraries

Third-party files copied into the repo and loaded with a plain `<script>` tag in
`index.html` (so they aren't npm packages and need no internet access at runtime).
The build copies them into the package along with the rest of `public/`.

| File | Library | Version | Licence | Source | SHA-256 |
|---|---|---|---|---|---|
| `plotly.min.js` | plotly.js | 4.0.0 | MIT | `plotly/package_data/plotly.min.js` inside the `plotly==7.0.0` wheel from PyPI (wheel hash verified against PyPI's published digest) | `14461f3b4c91c8bb590a99d6d03c3fd031ca40eec07ebab79a5e3eac107cd7ca` |

To upgrade: download the newer `plotly` wheel with
`pip download --no-deps --only-binary=:all: plotly==<version>`, check its age and
hash, extract `plotly/package_data/plotly.min.js`, and update this table.
