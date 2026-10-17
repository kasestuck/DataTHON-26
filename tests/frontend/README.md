# Browser QA without screenshots

The site itself needs only Python and local models. Browser QA additionally needs Node.js,
an installed Chrome, and Playwright 1.62.1 (the version verified in the local runtime).
Install the dev dependency with `npm --prefix tests/frontend install` when it is not already
available in your runtime. Alternatively set `NODE_PATH` to an existing trusted Node package
directory containing Playwright. No browser or model downloads happen in the QA scripts.

From the repository root:

```powershell
& .\.venv\Scripts\python.exe -m backend.scripts.smoke_fullstack
& .\.venv\Scripts\python.exe -m backend.scripts.smoke_fullstack --positive PATH_TO_POSITIVE.mov --negative PATH_TO_NEGATIVE.mov
```

The runner starts one Uvicorn on a random loopback port, serving only `frontend/`.
The first group intercepts API routes with explicit synthetic fixtures to test client behavior:
29 MiB upload, double-click, polling, done/failed, no_violation, invalid JSON/JPEG, request errors,
repeat upload, cancellation, page navigation, and widths 360/390/768/1440. No real AI is claimed
for these fixtures. `jpeg-fixtures.cjs` contains two tiny generated solid-color images.

With video arguments, a separate browser context uses the real browser-facing API and local AI.
Files go only to 127.0.0.1. The runner also submits a corrupt MOV. Reports contain only hashes,
counts, statuses and timings; no images, descriptions or secrets. Original files stay unchanged.
Reports are ignored by Git. Screenshots, videos and browser traces are not recorded.

This tests DOM layout constraints, not visual appearance or detector accuracy. Review the design
yourself. One positive and one negative video cannot establish quality or generalization.
