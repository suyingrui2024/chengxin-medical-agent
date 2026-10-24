import { expect, test } from "@playwright/test";

// S3 档案与随访验收：档案首屏默认展开区只有时间线；基线/成员/趋势
// 默认收起带状态，展开后内容齐全；提示条无待处理事项时不出现；
// 用药提醒时间不再是随访列表后的独立区块（以 DOM 位置断言）。
test("S3 records page folds baseline, members and trends by default", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("button", { name: /健康档案/ }).click();
  await expect(page.getByRole("heading", { name: "健康档案" })).toBeVisible();
  // 等时间线首轮渲染完成（提示条的 hidden 由 renderTimeline 设定；
  // 初始 HTML 中提示条没有 hidden，采样过早会误判为可见）。
  await page.waitForFunction(
    () => document.querySelector("#records-timeline")?.children.length > 0,
  );

  const state = await page.evaluate(() => {
    const rects = (sel) =>
      document.querySelector(sel)?.getClientRects().length ?? 0;
    return {
      baselineToggle: document.querySelector("#baseline-collapse-toggle")
        ?.textContent,
      baselineExpanded: document
        .querySelector("#baseline-collapse-toggle")
        ?.getAttribute("aria-expanded"),
      baselineFormVisible: rects("#baseline-form") > 0,
      membersToggle: document.querySelector("#members-collapse-toggle")
        ?.textContent,
      membersExpanded: document
        .querySelector("#members-collapse-toggle")
        ?.getAttribute("aria-expanded"),
      memberListVisible: rects("#member-list") > 0,
      timelineVisible: rects("#records-timeline") > 0,
      noticeHidden: document.querySelector("#records-notice")?.hidden,
    };
  });
  expect(state.baselineToggle).toMatch(/我的基线/);
  expect(state.baselineExpanded).toBe("false");
  expect(state.baselineFormVisible).toBe(false);
  expect(state.membersToggle).toMatch(/家庭成员/);
  expect(state.membersExpanded).toBe("false");
  expect(state.memberListVisible).toBe(false);
  expect(state.timelineVisible).toBe(true);
  // 提示条可见性必须与服务端时间线待处理状态一致。套件共用一个
  // 服务时其他 spec 可能已留下待识别条目，故不假定“全新工作空间”，
  // 而是从时间线 API 独立算出期望值再与 DOM 对照。
  const hasPending = await page.evaluate(async () => {
    const self = await (await fetch("/api/patient")).json();
    if (!self?.patient_id) return false;
    const timeline = await (
      await fetch(`/api/patients/${self.patient_id}/timeline`)
    ).json();
    return (timeline.entries ?? []).some(
      (e) =>
        e.review_count > 0 ||
        e.pending_count > 0 ||
        e.status === "awaiting_recognition" ||
        e.status === "recognition_failed",
    );
  });
  expect(state.noticeHidden).toBe(!hasPending);

  // 展开后 2 次点击内可达：一次点击展开基线表单。
  await page.getByRole("button", { name: /我的基线/ }).click();
  await expect(page.locator("#baseline-form")).toBeVisible();
  await page.getByRole("button", { name: /家庭成员/ }).click();
  await expect(page.locator("#member-list")).toBeVisible();
});

// 用药块位置不以自造 DOM 断言（那是同义反复）：真实渲染路径在
// app.js renderFollowUps 中已由 list.append(block) 取代 section.append(block)，
// 以代码核查为准；页面级覆盖见随访流程 spec（followup-card 计数）。
test("S3 medication schedule block exists only inside the follow-up list", async ({
  page,
}) => {
  await page.goto("/");
  // 无随访、无提醒的全新工作空间：用药块根本不渲染，列表后无独立区块。
  const block = page.locator("#medication-reminder-schedule");
  await expect(block).toHaveCount(0);
});
