<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Architecture rules
- All backend calls go through `src/lib/api.ts`; the base URL is the single `API_BASE_URL` constant, so the external FastAPI backend can be swapped in one place.
- Demo/mock data lives only in `src/mock/`; demo jobs are identified by the `demo-` job id prefix so pages work identically for demo and real jobs.
- Session state is in-memory (`src/lib/store.ts`) because the product forbids saved history.
- Data shapes in `src/lib/types.ts` mirror the backend OpenAPI contract exactly; don't add fields.
