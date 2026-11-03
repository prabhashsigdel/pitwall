import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { ApiError } from "./api/client";
import { Layout } from "./components/Layout";
import { RacePage } from "./features/race/RacePage";
import { ComingSoon, NotFound } from "./pages/ComingSoon";
import { LatestRace } from "./pages/LatestRace";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60_000, // race data only changes when the pipeline runs
      refetchOnWindowFocus: false,
      // Retry a busy database / network blip once; a refusal or 404 won't change.
      retry: (count, err) => count < 1 && !(err instanceof ApiError && ["refused", "not_found", "offline"].includes(err.kind)),
    },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <LatestRace /> },
      { path: "race", element: <LatestRace /> },
      { path: "race/:round", element: <RacePage /> },
      {
        path: "season",
        element: <ComingSoon label="Season" title="Season" text="Team and teammate tyre wear, undercut rates, fuel and sim accuracy across the season." />,
      },
      {
        path: "sim",
        element: <ComingSoon label="What if" title="Strategy simulator" text="Build another strategy for any driver and see where they would have finished." />,
      },
      {
        path: "circuits",
        element: <ComingSoon label="Circuits" title="Circuits" text="Safety car, VSC and red flag odds per circuit, 2022 to 2026." />,
      },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
