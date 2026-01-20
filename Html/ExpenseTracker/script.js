let total =0;

function addExpense()
{
    let name = document.getElementById("expenseName").value; 
    let amount = document.getElementById("expenseAmount").value; 

    if (name === "" || amount === "") {
        alert("Please enter all details");
        return;
    }

    let li = document.createElement("li");
    li.textContent =`${name} - ₹${amount}`;
    document.getElementById("expenseList").appendChild(li);

    total += parseInt(amount);
    document.getElementById("totalAmount").textContent = total;

    document.getElementById("expenseName").value = "";
    document.getElementById("expenseAmount").value = "";
}