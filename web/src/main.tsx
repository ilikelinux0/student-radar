import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./index.css";
import { readStored } from "./logic";
import PersonalApp from "./personal/PersonalApp";

const stored = readStored("radar-theme");
document.documentElement.classList.toggle("dark", stored !== "light");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PersonalApp />
  </StrictMode>,
);
