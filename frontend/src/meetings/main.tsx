import '../styles/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MeetingsApp } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MeetingsApp />
  </StrictMode>,
);
