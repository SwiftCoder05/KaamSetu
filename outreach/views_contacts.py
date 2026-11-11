from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from kaamsetu.throttle import hit

from .extract import ContactDraft, finalize_drafts, is_valid_email, parse_manual_input, summarize
from .files import FileExtractError, extract_contacts_from_file
from .models import Contact

MAX_IMPORT_ROWS = 5000


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["email", "name", "company", "role_title", "do_not_contact"]
        labels = {"role_title": "Role / title", "do_not_contact": "Do not contact"}

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if not is_valid_email(email):
            raise forms.ValidationError("Not a valid email address.")
        if Contact.objects.filter(owner=self.owner, email=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("You already have a contact with this email.")
        return email


@login_required
def contact_list(request):
    qs = Contact.objects.filter(owner=request.user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(name__icontains=q) | Q(company__icontains=q) | Q(role_title__icontains=q))
    if request.GET.get("filter") == "dnc":
        qs = qs.filter(do_not_contact=True)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "outreach/contacts.html", {"page": page, "q": q, "total": Contact.objects.filter(owner=request.user).count()})


@login_required
@require_POST
def contact_bulk(request):
    ids = request.POST.getlist("ids")
    action = request.POST.get("op")
    qs = Contact.objects.filter(owner=request.user, pk__in=ids)
    if not ids:
        messages.info(request, "Select at least one contact first.")
    elif action == "delete":
        n = qs.count()
        qs.delete()
        messages.success(request, f"Deleted {n} contact(s).")
    elif action in ("dnc_on", "dnc_off"):
        n = qs.update(do_not_contact=action == "dnc_on")
        messages.success(request, f"Updated {n} contact(s).")
    return redirect("contact_list")


@login_required
def contact_edit(request, pk):
    contact = get_object_or_404(Contact, pk=pk, owner=request.user)
    form = ContactForm(request.POST or None, instance=contact, owner=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Contact updated.")
        return redirect("contact_list")
    return render(request, "outreach/contact_edit.html", {"form": form, "contact": contact})


@login_required
@require_POST
def contact_delete(request, pk):
    get_object_or_404(Contact, pk=pk, owner=request.user).delete()
    messages.success(request, "Contact deleted.")
    return redirect("contact_list")


@login_required
def contact_import(request):
    """Step 1: paste or upload. Nothing is saved here; the user reviews a preview first."""
    if request.method == "GET":
        return render(request, "outreach/contact_import.html")

    if not hit(f"import:{request.user.pk}", limit=30, window_seconds=600):
        messages.error(request, "Too many imports in a short time. Wait a few minutes.")
        return render(request, "outreach/contact_import.html")

    existing = Contact.objects.filter(owner=request.user).values_list("email", flat=True)
    warnings: list[str] = []
    source = ""
    upload = request.FILES.get("file")
    pasted = request.POST.get("pasted", "")
    try:
        if upload:
            source = upload.name
            result = extract_contacts_from_file(upload.name, upload.read(), existing)
            drafts, warnings = result.drafts, result.warnings
        elif pasted.strip():
            source = "pasted text"
            drafts = parse_manual_input(pasted[:200_000], existing)
        else:
            messages.error(request, "Paste some email addresses or choose a file.")
            return render(request, "outreach/contact_import.html", {"pasted": pasted})
    except FileExtractError as e:
        messages.error(request, e.message)
        return render(request, "outreach/contact_import.html", {"pasted": pasted})

    if len(drafts) > MAX_IMPORT_ROWS:
        warnings.append(f"Only the first {MAX_IMPORT_ROWS} entries are shown.")
        drafts = drafts[:MAX_IMPORT_ROWS]
    return render(
        request,
        "outreach/contact_preview.html",
        {"drafts": drafts, "summary": summarize(drafts), "warnings": warnings, "source": source},
    )


@login_required
@require_POST
def contact_import_confirm(request):
    """Step 2: save only the rows the user ticked. Everything is validated again server-side."""
    try:
        total = min(int(request.POST.get("total", "0")), MAX_IMPORT_ROWS)
    except ValueError:
        total = 0
    picked: list[ContactDraft] = []
    for i in range(total):
        if request.POST.get(f"r{i}-include") != "1":
            continue
        email = request.POST.get(f"r{i}-email", "").strip().lower()
        picked.append(
            ContactDraft(
                email,
                name=request.POST.get(f"r{i}-name", "").strip()[:120],
                company=request.POST.get(f"r{i}-company", "").strip()[:120],
                role_title=request.POST.get(f"r{i}-role", "").strip()[:120],
                status="valid" if is_valid_email(email) else "invalid",
            )
        )
    existing = Contact.objects.filter(owner=request.user).values_list("email", flat=True)
    final = [d for d in finalize_drafts(picked, existing) if d.status == "valid"]
    Contact.objects.bulk_create(
        [Contact(owner=request.user, email=d.email, name=d.name, company=d.company, role_title=d.role_title) for d in final],
        ignore_conflicts=True,
    )
    skipped = len(picked) - len(final)
    msg = f"Imported {len(final)} contact(s)."
    if skipped:
        msg += f" {skipped} were skipped (invalid or already saved)."
    messages.success(request, msg)
    return redirect("contact_list")
