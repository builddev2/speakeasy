import '../styles/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { DiagnosticApp } from './App';

createRoot(document.getElementById('root')!).render(<StrictMode><DiagnosticApp /></StrictMode>);
