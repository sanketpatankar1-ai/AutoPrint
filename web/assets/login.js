const form = document.querySelector("#login"),
  msg = document.querySelector("#error");
document.querySelector("#reveal").onclick = (e) => {
  const show = form.password.type === "password";
  form.password.type = show ? "text" : "password";
  e.currentTarget.setAttribute(
    "aria-label",
    show ? "Hide password" : "Show password",
  );
};
form.onsubmit = async (e) => {
  e.preventDefault();
  const b = form.querySelector("[type=submit]");
  b.disabled = true;
  msg.textContent = "";
  try {
    await send("/api/login", "POST", {
      email: form.email.value,
      password: form.password.value,
    });
    location.replace("/owner" + (/^#orders\/AP-[A-F0-9]{10}$/.test(location.hash) ? location.hash : ""));
  } catch (err) {
    msg.textContent = err.message;
    b.disabled = false;
  }
};
