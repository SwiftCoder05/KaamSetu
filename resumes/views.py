import json
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from pydantic import ValidationError

from kaamsetu.throttle import hit
from outreach.files import FileExtractError, detect_file_kind, extract_document_text

from .ai.ats import score as ats_score
from .ai.guard import apply_tailoring
from .ai.provider import AiDisabledError, AiOutputError, AiQuotaError, AiUnavailableError, provider_from_settings, provider_label
from .ai.tailor import run_tailoring
from .export import page_count as export_page_count
from .export import safe_filename, to_docx, to_pdf
from .importer import parse_resume_text
from .models import Resume, ResumeVersion
from .preview import page_count, render_page_png
from .schema import SECTION_LABELS, ResumeData, parse_resume, resume_plain_text

RESUME_KINDS = {"pdf", "docx", "doc", "txt"}


def _version(request, vid) -> ResumeVersion:
    return get_object_or_404(ResumeVersion.objects.select_related("resume"), pk=vid, resume__owner=request.user)


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


@login_required
def resume_list(request):
    """Resume studio home: import or start a resume. The saved resumes live on their own page."""
    resumes = Resume.objects.filter(owner=request.user)
    return render(request, "resumes/list.html", {"count": resumes.count(), "latest": resumes.first(),
                                                 "max_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024)})


@login_required
def resume_all(request):
    resumes = Resume.objects.filter(owner=request.user).prefetch_related("versions")
    return render(request, "resumes/all.html", {"resumes": resumes})


@login_required
@require_POST
def resume_new(request):
    title = request.POST.get("title", "").strip()[:160]
    upload = request.FILES.get("file")
    if not upload:
        resume = Resume.objects.create(owner=request.user, title=title or "My resume")
        data = ResumeData()
        data.basics.name = request.user.profile.full_name[:120]
        data.basics.email = request.user.email[:254]
        v = ResumeVersion.objects.create(resume=resume, label="Version 1", data=data.model_dump(), source=ResumeVersion.Source.BLANK)
        return redirect(f"{reverse('resume_workspace', args=[resume.pk])}?v={v.pk}")

    raw = upload.read()
    try:
        if len(raw) > settings.MAX_UPLOAD_BYTES:
            raise FileExtractError("too_large", f"The file is larger than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        if not raw:
            raise FileExtractError("empty", "The file is empty.")
        kind = detect_file_kind(upload.name, raw)
        if kind not in RESUME_KINDS:
            raise FileExtractError("unsupported_type", "Upload your resume as PDF, DOCX, DOC or TXT.")
        text = extract_document_text(kind, raw)
    except FileExtractError as e:
        messages.error(request, e.message)
        return redirect("resume_list")

    data, notes = parse_resume_text(text)
    if kind == "doc":
        notes.insert(0, "Old .doc files only give rough text. For a cleaner import, save the file as DOCX in Word and import that.")
    resume = Resume(owner=request.user, title=title or upload.name.rsplit(".", 1)[0][:160], original_name=upload.name[:255],
                    original_size=len(raw), import_notes=notes)
    resume.original_file.save(upload.name, ContentFile(raw), save=False)
    resume.build_preview(kind, raw, save=False)  # so the studio can show the file exactly as it looks
    resume.save()
    # The imported version is locked: it is the untouched source of truth; edits become new versions.
    v = ResumeVersion.objects.create(resume=resume, label="Imported original", data=data.model_dump(), source=ResumeVersion.Source.IMPORT, locked=True)
    messages.info(request, "Imported. Review each section: the import is a best guess from the file's text.")
    return redirect(f"{reverse('resume_workspace', args=[resume.pk])}?v={v.pk}")


@login_required
def resume_workspace(request, pk):
    resume = get_object_or_404(Resume, pk=pk, owner=request.user)
    versions = list(resume.versions.all())
    if not versions:
        raise Http404
    current = next((v for v in versions if str(v.pk) == request.GET.get("v")), versions[0])
    pdf = resume.preview_pdf_bytes() if resume.original_file else None
    pages = page_count(pdf) if pdf else 0
    return render(
        request,
        "resumes/workspace.html",
        {
            "original_pages": range(pages),
            "original_html": resume.preview_html if not pages else "",
            "show_original_first": bool(pages or resume.preview_html) and current.source == ResumeVersion.Source.IMPORT,
            "resume": resume,
            "versions": versions,
            "v": current,
            "data": current.parsed().model_dump(),
            "labels": SECTION_LABELS,
            "ai_enabled": request.user.profile.ai_enabled,
            "ai_label": provider_label(),
        },
    )


@login_required
@require_POST
def version_save(request, vid):
    v = _version(request, vid)
    body = _json_body(request)
    try:
        data = parse_resume(body.get("data"))
    except ValidationError as e:
        first = e.errors()[0]
        where = " › ".join(str(x) for x in first["loc"])
        return JsonResponse({"error": f"Could not save: {where}: {first['msg']}"}, status=400)
    label = str(body.get("label") or "").strip()[:160]
    if v.locked or body.get("as_new"):
        n = v.resume.versions.count() + 1
        nv = ResumeVersion.objects.create(resume=v.resume, label=label or f"Version {n}", data=data.model_dump(), source=ResumeVersion.Source.MANUAL)
        v.resume.save(update_fields=["updated_at"])
        return JsonResponse({"ok": True, "created": True, "url": f"{reverse('resume_workspace', args=[v.resume_id])}?v={nv.pk}"})
    v.data = data.model_dump()
    if label:
        v.label = label
    v.save()
    v.resume.save(update_fields=["updated_at"])
    return JsonResponse({"ok": True, "created": False})


@login_required
def version_export(request, vid, fmt):
    v = _version(request, vid)
    data = v.parsed()
    name = data.basics.name or v.resume.title
    if fmt == "pdf":
        content, ctype = to_pdf(data), "application/pdf"
    elif fmt == "docx":
        content, ctype = to_docx(data), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise Http404
    resp = HttpResponse(content, content_type=ctype)
    resp["Content-Disposition"] = f'attachment; filename="{safe_filename(name, fmt)}"'
    return resp


@login_required
def resume_original(request, pk):
    resume = get_object_or_404(Resume, pk=pk, owner=request.user)
    if not resume.original_file:
        raise Http404
    ctype = mimetypes.guess_type(resume.original_name)[0] or "application/octet-stream"
    return FileResponse(resume.original_file.open("rb"), as_attachment=True, filename=resume.original_name, content_type=ctype)


@login_required
def resume_original_page(request, pk, n):
    """One page of the original file as an image, owner-checked and never public."""
    resume = get_object_or_404(Resume, pk=pk, owner=request.user)
    key = f"resume-page:{resume.pk}:{resume.updated_at.timestamp()}:{n}"
    png = cache.get(key)
    if png is None:
        pdf = resume.preview_pdf_bytes()
        png = render_page_png(pdf, n) if pdf and n < 20 else None
        if png is None:
            raise Http404
        cache.set(key, png, 60 * 60)
    response = HttpResponse(png, content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
@require_POST
def version_delete(request, vid):
    v = _version(request, vid)
    if v.locked:
        messages.error(request, "The imported original is locked and cannot be deleted.")
    elif v.resume.versions.count() <= 1:
        messages.error(request, "A resume needs at least one version.")
    else:
        rid = v.resume_id
        v.delete()
        messages.success(request, "Version deleted.")
        return redirect("resume_workspace", pk=rid)
    return redirect(f"{reverse('resume_workspace', args=[v.resume_id])}?v={v.pk}")


@login_required
@require_POST
def resume_delete(request, pk):
    resume = get_object_or_404(Resume, pk=pk, owner=request.user)
    for f in (resume.original_file, resume.preview_pdf):
        if f:
            f.delete(save=False)
    resume.delete()
    messages.success(request, "Resume deleted.")
    return redirect("resume_all")


@login_required
@require_POST
def version_tailor(request, vid):
    v = _version(request, vid)
    body = _json_body(request)
    jd = str(body.get("job_description", "")).strip()
    if len(jd) < 40:
        return JsonResponse({"error": "Paste the full job description (at least a few sentences)."}, status=400)
    if not hit(f"ai:{request.user.pk}", limit=12, window_seconds=3600):
        return JsonResponse({"error": "You've reached the hourly AI limit. Try again later."}, status=429)
    try:
        result = run_tailoring(
            ai_enabled=request.user.profile.ai_enabled,
            confirmed=body.get("confirm") is True,  # only the explicit button sends true
            resume=v.parsed(),
            job_description=jd[:12000],
            instructions=str(body.get("instructions", ""))[:1000],
            provider=provider_from_settings(),
        )
    except AiDisabledError as e:
        return JsonResponse({"error": str(e)}, status=403)
    except AiQuotaError as e:
        return JsonResponse({"error": str(e)}, status=429)
    except (AiUnavailableError, AiOutputError) as e:
        return JsonResponse({"error": str(e)}, status=502)
    return JsonResponse(result)


def _keywords_from(body) -> list[dict]:
    """Keywords echoed back by the browser (from the analysis). Only used to show the user their own score."""
    out = []
    for k in body.get("keywords", [])[:40]:
        if isinstance(k, dict) and str(k.get("term", "")).strip():
            out.append({"term": str(k["term"]).strip()[:80], "importance": "required" if k.get("importance") == "required" else "preferred"})
    return out


def _accepted_from(body) -> list[dict]:
    return [s for s in body.get("suggestions", []) if isinstance(s, dict)][:60]


def _build_tailored(v, accepted):
    nxt = apply_tailoring(v.parsed(), accepted)
    return parse_resume(nxt.model_dump())


@login_required
@require_POST
def version_tailor_check(request, vid):
    """Live feedback while the user accepts/declines: the ATS score and page count of the result so far."""
    v = _version(request, vid)
    body = _json_body(request)
    try:
        nxt = _build_tailored(v, [s for s in _accepted_from(body) if s.get("state") != "blocked"])
    except (ValidationError, ValueError) as e:
        return JsonResponse({"error": str(e) if isinstance(e, ValueError) else "A suggestion is too long."}, status=400)
    result = ats_score(resume_plain_text(nxt), _keywords_from(body))
    return JsonResponse({**result, "pages": export_page_count(nxt)})


@login_required
@require_POST
def version_apply(request, vid):
    """Creates the final tailored resume as a NEW version; the source version is never modified."""
    v = _version(request, vid)
    body = _json_body(request)
    accepted = _accepted_from(body)
    if not accepted:
        return JsonResponse({"error": "Accept at least one suggestion first."}, status=400)
    try:
        nxt = _build_tailored(v, accepted)
    except ValidationError:
        return JsonResponse({"error": "A suggestion was too long to apply. Shorten it and try again."}, status=400)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    pages = export_page_count(nxt)
    if pages > 1:
        return JsonResponse({"error": f"With these changes the resume would be {pages} pages even with the tightest spacing. "
                                      "Decline a few added lines or skills, or shorten some text, to keep it to one page."}, status=400)
    job_title = str(body.get("job_title", "")).strip()[:160]
    company = str(body.get("company", "")).strip()[:160]
    label = "Tailored" + (f" for {job_title}" if job_title else "") + (f" at {company}" if company else "")
    nv = ResumeVersion.objects.create(resume=v.resume, label=label[:160], data=nxt.model_dump(), source=ResumeVersion.Source.AI,
                                      job_title=job_title, company=company)
    final = ats_score(resume_plain_text(nxt), _keywords_from(body))
    messages.success(request, f"Tailored resume created: ATS keyword match {final['score']}%, one page.")
    return JsonResponse({"ok": True, "score": final["score"], "url": f"{reverse('resume_workspace', args=[v.resume_id])}?v={nv.pk}"})
