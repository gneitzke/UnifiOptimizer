import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

// Self-hosted InterVariable (docs §Licensing/§Typography) — bundled by Vite,
// no CDN. The opsz axis backs `font-optical-sizing: auto`.
import '@fontsource-variable/inter/opsz.css';

// Design tokens, Tailwind bridge, type roles. Importing the theme store applies
// the initial [data-theme] before first paint.
import './index.css';
import './theme';

import App from './App.tsx';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
