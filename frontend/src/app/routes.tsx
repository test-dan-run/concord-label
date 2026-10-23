import { createBrowserRouter, Navigate, Outlet, useRouteError } from "react-router-dom";
import { Nav } from "./Nav";
import { DatasetDetail } from "../features/datasets/DatasetDetail";
import { DatasetsPage } from "../features/datasets/DatasetsPage";
import { UploadDataset } from "../features/datasets/UploadDataset";
import { ModelsPage } from "../features/models/ModelsPage";
import { DocsPage } from "../features/docs/DocsPage";
import { JobsPage } from "../features/jobs/JobsPage";
import { LexiconsPage } from "../features/lexicons/LexiconsPage";
import { AnnotatePage, OpenFile, OpenFileBySha } from "../features/label/Annotate";
import { LiveEvalPage } from "../features/label/LiveEval";
import { PseudoLabelPage } from "../features/label/PseudoLabel";
import { ReviewConsole } from "../features/label/ReviewConsole";
import { SyncPage } from "../features/label/SyncPage";

// Improve (Phase 3) is on hold and in no image, so there is no Training route (SPLIT_PLAN.md §3.2).

function Layout() {
  return (
    <div className="min-h-screen bg-canvas text-fg flex flex-col lg:flex-row">
      <Nav />
      <main className="flex-1 min-w-0 px-4 py-6 sm:px-8 sm:pt-7 sm:pb-10 flex flex-col gap-5">
        <Outlet />
      </main>
    </div>
  );
}

function RouteError() {
  const err = useRouteError() as Error;
  return (
    <div className="p-8 text-fg">
      <h1 className="text-lg">Something went wrong</h1>
      <pre className="text-muted text-xs whitespace-pre-wrap">{err?.message ?? String(err)}</pre>
    </div>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/label" replace /> },
      { path: "label", element: <ReviewConsole /> },
      { path: "label/pseudo", element: <PseudoLabelPage /> },
      { path: "label/pseudo/:datasetId", element: <PseudoLabelPage /> },
      { path: "label/annotate", element: <AnnotatePage /> },
      { path: "label/annotate/:datasetId", element: <AnnotatePage /> },
      { path: "label/file/by-sha/:sha256", element: <OpenFileBySha /> },
      { path: "label/file/:fileId", element: <OpenFile /> },
      { path: "label/live", element: <LiveEvalPage /> },
      { path: "label/live/:datasetId", element: <LiveEvalPage /> },
      { path: "label/sync", element: <SyncPage /> },
      { path: "datasets", element: <DatasetsPage /> },
      { path: "datasets/upload", element: <UploadDataset /> },
      { path: "datasets/:id", element: <DatasetDetail /> },
      { path: "models", element: <ModelsPage /> },
      // Registering a model and its contract page moved to the model catalog's own UI.
      { path: "models/add", element: <Navigate to="/models" replace /> },
      { path: "contract", element: <Navigate to="/models" replace /> },
      { path: "jobs", element: <JobsPage /> },
      { path: "lexicons", element: <LexiconsPage /> },
      { path: "lexicons/:id", element: <LexiconsPage /> },
      { path: "docs", element: <DocsPage /> },
      { path: "*", element: <Navigate to="/label" replace /> },
    ],
  },
]);
