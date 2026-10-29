import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { LineChart } from "./ui";

it("replays the line when data changes while preserving the accessible final balance", () => {
  const {container,rerender}=render(<LineChart values={[10000,20000,15000]} labels={["10/01","10/02","10/03"]}/>);
  const line=container.querySelector("polyline");
  expect(screen.getByRole("img",{name:"账户余额趋势，期末 150.00 元"})).toBeInTheDocument();
  expect(container.querySelector(".chart-reveal")).toBeInTheDocument();
  rerender(<LineChart values={[10000,12000,18000]}/>);
  expect(container.querySelector("polyline")).not.toBe(line);
  expect(screen.getByRole("img",{name:"账户余额趋势，期末 180.00 元"})).toBeInTheDocument();
});

it("gives multiple charts independent gradient references, including charts inside conversations", () => {
  const {container}=render(<><LineChart values={[100,200]}/><LineChart values={[300,400]}/></>);
  const gradients=[...container.querySelectorAll("linearGradient")].map(g=>g.id);
  expect(new Set(gradients).size).toBe(2);
  const fills=[...container.querySelectorAll("polygon")].map(p=>p.getAttribute("fill"));
  expect(fills).toEqual(gradients.map(id=>`url(#${id})`));
});
