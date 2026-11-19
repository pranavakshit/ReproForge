use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

pub fn find_repo_root() -> Option<PathBuf> {
    if let Ok(root_env) = std::env::var("REPROFORGE_ROOT") {
        let p = PathBuf::from(root_env);
        if p.exists() {
            return Some(p);
        }
    }

    // Check cwd
    if let Ok(cwd) = std::env::current_dir() {
        let mut curr = Some(cwd.as_path());
        while let Some(dir) = curr {
            if dir.join("backend").join("app").exists() || dir.join("pyproject.toml").exists() {
                return Some(dir.to_path_buf());
            }
            curr = dir.parent();
        }
    }

    // Check relative to current exe
    if let Ok(exe_path) = std::env::current_exe() {
        let mut curr = exe_path.parent();
        while let Some(dir) = curr {
            if dir.join("backend").join("app").exists() || dir.join("pyproject.toml").exists() {
                return Some(dir.to_path_buf());
            }
            curr = dir.parent();
        }
    }

    None
}

pub fn find_python_interpreter() -> Option<PathBuf> {
    if let Ok(custom) = std::env::var("REPROFORGE_PYTHON") {
        let p = PathBuf::from(custom);
        if p.is_file() {
            return Some(p);
        }
    }

    let repo_root = find_repo_root();

    if let Some(ref root) = repo_root {
        #[cfg(target_os = "windows")]
        let venv_py = root.join(".venv").join("Scripts").join("python.exe");

        #[cfg(not(target_os = "windows"))]
        let venv_py = root.join(".venv").join("bin").join("python");

        if venv_py.is_file() {
            return Some(venv_py);
        }
    }

    // Fallback to checking standard PATH
    #[cfg(target_os = "windows")]
    let candidates = ["python.exe", "python3.exe", "python"];
    #[cfg(not(target_os = "windows"))]
    let candidates = ["python3", "python"];

    for candidate in candidates {
        if let Ok(output) = Command::new(candidate).arg("--version").output() {
            if output.status.success() {
                return Some(PathBuf::from(candidate));
            }
        }
    }

    None
}

pub fn spawn_engine_daemon() -> Result<(Child, PathBuf), String> {
    let python = find_python_interpreter()
        .ok_or_else(|| "Could not locate Python interpreter in .venv or PATH".to_string())?;

    let root = find_repo_root()
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));

    let mut cmd = Command::new(&python);
    cmd.args(["-m", "backend.app.cli.main", "daemon"])
        .current_dir(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    // On Windows, detach or prevent popup windows if desired
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn daemon using {:?}: {}", python, e))?;

    Ok((child, python))
}
